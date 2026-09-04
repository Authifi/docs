provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

# Canonical's official images. The account ID is pinned because an AMI name is
# not a trust boundary: anyone may publish an image called
# "ubuntu/images/...-server-*".
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  github_repository_subject = "repo:${var.github_repository}:ref:refs/heads/${var.deploy_branch}"
  github_oidc_provider_arn  = coalesce(var.existing_github_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
  release_bucket_name       = coalesce(var.release_bucket_name, "${var.service_name}-releases-${data.aws_caller_identity.current.account_id}")

  common_tags = merge(var.tags, {
    ManagedBy  = "Terraform"
    Repository = var.github_repository
    Service    = var.service_name
  })

  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    oidc_issuer      = var.oidc_issuer
    oidc_client_id   = var.oidc_client_id
    public_base_url  = var.public_base_url
    site_dir         = var.site_dir
    post_logout_path = var.post_logout_path
    app_port         = var.app_port

    # The installer is bash. Base64 is what keeps its own `${...}` out of
    # Terraform's template language and out of the heredoc that carries it.
    deploy_script_base64 = base64encode(file("${path.module}/scripts/deploy-release.sh"))
  })
}

# --- The shared network this root consumes ------------------------------------

# Authifi's VPC, its two public subnets, and the private application subnet
# whose route table already points at the shared NAT Gateway. That egress is
# what makes server-side OIDC discovery and the authorization-code exchange
# possible, so this root reads the network rather than building a second one.
data "aws_vpc" "shared" {
  id = var.vpc_id
}

# Three IDs can each exist and still not belong together. Left unchecked, the
# mismatch surfaces at apply time as an AWS error about the load balancer or the
# instance; the postconditions turn it into a message naming the variable.
data "aws_subnet" "public" {
  for_each = toset(var.public_subnet_ids)
  id       = each.value

  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.vpc_id
      error_message = "Every public subnet must belong to vpc_id."
    }
  }
}

data "aws_subnet" "app" {
  id = var.private_app_subnet_id

  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.vpc_id
      error_message = "private_app_subnet_id must belong to vpc_id."
    }
  }
}

# --- Security groups ----------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.service_name}-alb"
  description = "Public load balancer for the Authifi docs site"
  vpc_id      = var.vpc_id

  tags = merge(local.common_tags, { Name = "${var.service_name}-alb" })
}

resource "aws_security_group" "app" {
  name        = "${var.service_name}-app"
  description = "Private docs instance"
  vpc_id      = var.vpc_id

  tags = merge(local.common_tags, { Name = "${var.service_name}-app" })
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_from_internet" {
  security_group_id = aws_security_group.alb.id
  description       = "Redirect or bootstrap response on plain HTTP"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_from_internet" {
  security_group_id = aws_security_group.alb.id
  description       = "Public HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forwarded requests and health checks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

# The application port is reachable from the load balancer's security group and
# from nothing else. Naming a CIDR here, even the VPC's own, would open the port
# to anything that later lands in the VPC.
resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "Load balancer to uvicorn"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.app_port
  to_port                      = var.app_port
}

# Egress leaves through the private subnet's existing shared NAT route. It is
# named port by port rather than opened wholesale: `ip_protocol = "-1"` would
# cover all four flows below and every other outbound port as well.
#
# DNS is the one flow that stays inside the VPC. Sending it to an external
# resolver would break the VPC's own private hosted zones.
resource "aws_vpc_security_group_egress_rule" "app_dns_udp" {
  security_group_id = aws_security_group.app.id

  description = "VPC resolver"
  cidr_ipv4   = data.aws_vpc.shared.cidr_block
  ip_protocol = "udp"
  from_port   = 53
  to_port     = 53
}

# Truncated answers and zone transfers fall back to TCP.
resource "aws_vpc_security_group_egress_rule" "app_dns_tcp" {
  security_group_id = aws_security_group.app.id

  description = "VPC resolver over TCP"
  cidr_ipv4   = data.aws_vpc.shared.cidr_block
  ip_protocol = "tcp"
  from_port   = 53
  to_port     = 53
}

# Ubuntu's archive mirrors are plain HTTP; the packages are signature-checked by
# apt rather than by the transport. This is what lets first boot install the
# python3-venv package the cloud image leaves out.
resource "aws_vpc_security_group_egress_rule" "app_http" {
  security_group_id = aws_security_group.app.id

  description = "Ubuntu package mirrors during first-boot bootstrap"
  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "tcp"
  from_port   = 80
  to_port     = 80
}

# OIDC discovery, signing-key retrieval, and the authorization-code exchange,
# all of which the docs server performs itself, plus Systems Manager and S3.
resource "aws_vpc_security_group_egress_rule" "app_https" {
  security_group_id = aws_security_group.app.id

  description = "OIDC, Systems Manager, S3, and package updates"
  cidr_ipv4   = "0.0.0.0/0"
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
}

# Clock skew shows up as an OIDC failure rather than a clock failure, because
# the ID token's `iat` and `exp` checks are the first thing it breaks.
resource "aws_vpc_security_group_egress_rule" "app_time_sync" {
  security_group_id = aws_security_group.app.id

  description = "Amazon Time Sync Service"
  cidr_ipv4   = "169.254.169.123/32"
  ip_protocol = "udp"
  from_port   = 123
  to_port     = 123
}

# --- Public edge --------------------------------------------------------------

resource "aws_lb" "docs" {
  name               = var.service_name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  drop_invalid_header_fields = true

  tags = local.common_tags

  lifecycle {
    # Two distinct subnet IDs are not two availability zones. Referencing the
    # lookups here also orders them ahead of this load balancer, so a subnet
    # from the wrong VPC fails their postconditions during plan rather than
    # failing this resource during apply.
    precondition {
      condition     = length(distinct([for subnet in data.aws_subnet.public : subnet.availability_zone])) == 2
      error_message = "public_subnet_ids must name subnets in two different availability zones."
    }
  }
}

resource "aws_lb_target_group" "docs" {
  name     = var.service_name
  port     = var.app_port
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    enabled             = true
    protocol            = "HTTP"
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }

  tags = local.common_tags
}

resource "aws_lb_target_group_attachment" "docs" {
  target_group_arn = aws_lb_target_group.docs.arn
  target_id        = aws_instance.docs.id
  port             = var.app_port
}

# Requested, not validated. The validation records land in DNS this root does
# not manage, so `aws_acm_certificate_validation` would deadlock the first
# apply against records that cannot exist yet.
resource "aws_acm_certificate" "docs" {
  domain_name       = var.custom_domain_name
  validation_method = "DNS"

  tags = local.common_tags

  lifecycle {
    create_before_destroy = true
  }
}

# The two-stage switch below exists because of that gap. AWS refuses to attach a
# pending certificate to an HTTPS listener, so the first apply answers port 80
# with a holding response, and the apply after DNS validation reports ISSUED
# turns it into a redirect and adds the HTTPS listener.
#
# One listener owns port 80 unconditionally, and only its default action is
# conditional. Two count-gated listeners would make the flip a create racing a
# destroy on the same port, which AWS rejects with DuplicateListener; this is an
# in-place update, so the flip lands in a single apply. The two `for_each`
# expressions are exact inverses, so the listener always has one action.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.docs.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = var.enable_https_listener ? [1] : []

    content {
      type = "redirect"

      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = var.enable_https_listener ? [] : [1]

    content {
      type = "fixed-response"

      fixed_response {
        content_type = "text/plain"
        message_body = "HTTPS certificate validation is pending"
        status_code  = "503"
      }
    }
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "https" {
  count             = var.enable_https_listener ? 1 : 0
  load_balancer_arn = aws_lb.docs.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.docs.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.docs.arn
  }

  tags = local.common_tags
}

# --- Release storage ----------------------------------------------------------

resource "aws_s3_bucket" "releases" {
  bucket = local.release_bucket_name

  tags = merge(local.common_tags, { Name = local.release_bucket_name })
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket = aws_s3_bucket.releases.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }

    bucket_key_enabled = true
  }
}

# A release SHA is only immutable if the object under it is. Versioning is what
# makes an overwritten archive recoverable and auditable rather than silent.
resource "aws_s3_bucket_versioning" "releases" {
  bucket = aws_s3_bucket.releases.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id

  rule {
    id     = "expire-releases"
    status = "Enabled"

    filter {}

    expiration {
      days = var.release_retention_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.release_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.releases]
}

# --- Instance identity --------------------------------------------------------

data "aws_iam_policy_document" "instance_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.service_name}-instance"
  assume_role_policy = data.aws_iam_policy_document.instance_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "instance_ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Read, and only under releases/. The instance never writes to the bucket: the
# workflow uploads, and this role's job is to fetch what was uploaded.
data "aws_iam_policy_document" "instance_releases" {
  statement {
    sid       = "AllowReadReleaseObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }
}

resource "aws_iam_policy" "instance_releases" {
  name   = "${var.service_name}-instance-releases"
  policy = data.aws_iam_policy_document.instance_releases.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "instance_releases" {
  role       = aws_iam_role.instance.name
  policy_arn = aws_iam_policy.instance_releases.arn
}

resource "aws_iam_instance_profile" "docs" {
  name = "${var.service_name}-instance"
  role = aws_iam_role.instance.name
  tags = local.common_tags
}

# --- The docs instance --------------------------------------------------------

# No public address and no inbound administrative port. Outbound traffic leaves
# through the private subnet's shared NAT route; inbound reaches the application
# port only from the load balancer's security group.
#
# Cloud-init runs user data once, on first boot, so `user_data_replace_on_change`
# is what keeps a templated value from changing the plan while changing nothing
# on the host — the wrong failure mode for the OIDC client and the site paths.
# Replacement regenerates the session secret and empties the release directory,
# so a redeploy has to follow one.
resource "aws_instance" "docs" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = var.private_app_subnet_id
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.docs.name
  associate_public_ip_address = false
  user_data                   = local.user_data
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"

    # IMDSv1 turns any request-forgery bug in the docs server into a read of
    # this instance's role credentials.
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size           = var.root_volume_size_gib
    volume_type           = "gp3"
    delete_on_termination = true

    # The release tree, the environment file, and the session key all live here.
    encrypted = true
  }

  tags = merge(local.common_tags, { Name = var.service_name })

  lifecycle {
    # A public subnet ID is a perfectly valid string for this variable. Failing
    # the plan is better than quietly placing the docs server in a subnet that
    # routes to an internet gateway.
    precondition {
      condition     = data.aws_subnet.app.map_public_ip_on_launch == false
      error_message = "private_app_subnet_id must name a private subnet; this one auto-assigns public addresses."
    }
  }

  # First boot does two things that need the network already permitted: it
  # installs a package over HTTP, and the agent registers with Systems Manager
  # over HTTPS. Terraform would otherwise be free to create the instance
  # alongside these rules, leaving a host whose user data aborted and a node
  # that never becomes managed until something restarts the agent.
  depends_on = [
    aws_iam_role_policy_attachment.instance_ssm_core,
    aws_iam_role_policy_attachment.instance_releases,
    aws_vpc_security_group_egress_rule.app_dns_udp,
    aws_vpc_security_group_egress_rule.app_dns_tcp,
    aws_vpc_security_group_egress_rule.app_http,
    aws_vpc_security_group_egress_rule.app_https,
  ]
}

# --- Deployment through Systems Manager ---------------------------------------

# The instance has no AWS CLI and no internet, so the agent itself stages both
# objects. `aws:downloadContent` treats a destination that is not a directory as
# a filename, which is why both destinations end in a separator: without it the
# archive would land at incoming/<sha> and the checksum step would overwrite it.
#
# ReleaseSha is interpolated into a root shell command, so `allowedPattern` is
# the boundary that keeps it a git SHA. Systems Manager rejects the SendCommand
# before the document runs.
resource "aws_ssm_document" "deploy" {
  name            = "${var.service_name}-deploy"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"
  tags            = local.common_tags

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Install an immutable Authifi docs release"
    parameters = {
      ReleaseSha = {
        type           = "String"
        description    = "The 40-character lowercase git SHA of the release to install"
        allowedPattern = "^[0-9a-f]{40}$"
      }
    }
    mainSteps = [
      {
        action = "aws:downloadContent"
        name   = "downloadArchive"
        inputs = {
          sourceType = "S3"
          sourceInfo = jsonencode({
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.amazonaws.com/releases/{{ ReleaseSha }}.tar.gz"
          })
          destinationPath = "/opt/authifi-docs/incoming/{{ ReleaseSha }}/"
        }
      },
      {
        action = "aws:downloadContent"
        name   = "downloadChecksum"
        inputs = {
          sourceType = "S3"
          sourceInfo = jsonencode({
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.amazonaws.com/releases/{{ ReleaseSha }}.tar.gz.sha256"
          })
          destinationPath = "/opt/authifi-docs/incoming/{{ ReleaseSha }}/"
        }
      },
      {
        action = "aws:runShellScript"
        name   = "installRelease"
        inputs = {
          runCommand = [
            "/usr/local/sbin/authifi-docs-deploy '{{ ReleaseSha }}'",
          ]
        }
      },
    ]
  })
}

# --- The GitHub Actions deployment role ---------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  count = var.existing_github_oidc_provider_arn == null ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com",
  ]

  tags = local.common_tags
}

data "aws_iam_policy_document" "github_deploy_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    actions = ["sts:AssumeRoleWithWebIdentity"]

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringEquals, not StringLike: a pattern here would let any ref whose name
    # happens to match assume the role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_repository_subject]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.service_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "AllowReleaseObjectAccess"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }

  # Naming both the document and the instance is what keeps this from being
  # remote root on every managed node in the account.
  statement {
    sid     = "AllowDeployCommand"
    effect  = "Allow"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_ssm_document.deploy.arn,
      aws_instance.docs.arn,
    ]
  }

  # Read-only, and on "*" because neither API accepts an instance or command
  # resource ARN.
  statement {
    sid    = "AllowDeployCommandStatus"
    effect = "Allow"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]
  }

  # Also "*": DescribeTargetHealth's only documented resource scope.
  statement {
    sid       = "AllowTargetHealthCheck"
    effect    = "Allow"
    actions   = ["elasticloadbalancing:DescribeTargetHealth"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "github_deploy" {
  name   = "${var.service_name}-github-deploy"
  policy = data.aws_iam_policy_document.github_deploy.json
  tags   = local.common_tags
}

resource "aws_iam_role_policy_attachment" "github_deploy" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy.arn
}

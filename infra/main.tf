provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

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

# --- Network ------------------------------------------------------------------

resource "aws_vpc" "docs" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, { Name = var.service_name })
}

resource "aws_internet_gateway" "docs" {
  vpc_id = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = var.service_name })
}

# Two, in two availability zones, because that is the minimum an internet-facing
# application load balancer accepts.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.docs.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, { Name = "${var.service_name}-public-${count.index}" })
}

resource "aws_subnet" "app" {
  vpc_id                  = aws_vpc.docs.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false

  tags = merge(local.common_tags, { Name = "${var.service_name}-app" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = "${var.service_name}-public" })
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.docs.id
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Deliberately routeless apart from the VPC's own range and the S3 gateway
# endpoint's prefix list. Adding a route here is how this instance would get an
# unreviewed path to the internet.
resource "aws_route_table" "app" {
  vpc_id = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = "${var.service_name}-app" })
}

resource "aws_route_table_association" "app" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.app.id
}

# --- Security groups ----------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${var.service_name}-alb"
  description = "Public load balancer for the Authifi docs site"
  vpc_id      = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = "${var.service_name}-alb" })
}

resource "aws_security_group" "app" {
  name        = "${var.service_name}-app"
  description = "Private docs instance"
  vpc_id      = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = "${var.service_name}-app" })
}

resource "aws_security_group" "endpoints" {
  name        = "${var.service_name}-endpoints"
  description = "Interface VPC endpoints for Systems Manager"
  vpc_id      = aws_vpc.docs.id

  tags = merge(local.common_tags, { Name = "${var.service_name}-endpoints" })
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

resource "aws_vpc_security_group_egress_rule" "app_to_endpoints" {
  security_group_id            = aws_security_group.app.id
  description                  = "Systems Manager interface endpoints"
  referenced_security_group_id = aws_security_group.endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# A gateway endpoint leaves S3's public addresses in place and routes them
# privately, so a security group that only allowed the interface endpoints would
# let SSM stage nothing at all.
resource "aws_vpc_security_group_egress_rule" "app_to_s3" {
  security_group_id = aws_security_group.app.id
  description       = "Release archives through the S3 gateway endpoint"
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Clock skew shows up as an OIDC failure rather than a clock failure, because
# the ID token's `iat`, `exp`, and `nonce` checks are the first thing it breaks.
resource "aws_vpc_security_group_egress_rule" "app_to_time_sync" {
  security_group_id = aws_security_group.app.id
  description       = "Amazon Time Sync Service"
  cidr_ipv4         = "169.254.169.123/32"
  ip_protocol       = "udp"
  from_port         = 123
  to_port           = 123
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_app" {
  security_group_id            = aws_security_group.endpoints.id
  description                  = "Docs instance to Systems Manager"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# --- Public edge --------------------------------------------------------------

resource "aws_lb" "docs" {
  name               = var.service_name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true

  tags = local.common_tags
}

resource "aws_lb_target_group" "docs" {
  name     = var.service_name
  port     = var.app_port
  protocol = "HTTP"
  vpc_id   = aws_vpc.docs.id

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
# pending certificate to an HTTPS listener, so the first apply serves the
# bootstrap response on port 80, and the apply after DNS validation reports
# ISSUED replaces it with the redirect and the HTTPS listener.
#
# Both port-80 listeners are separate resources with no dependency between them,
# so that apply may try to create this one before it has finished destroying
# `bootstrap` and fail with DuplicateListener. Re-running apply converges,
# because by then only one of the two is left to do.
resource "aws_lb_listener" "http" {
  count             = var.enable_https_listener ? 1 : 0
  load_balancer_arn = aws_lb.docs.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
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

resource "aws_lb_listener" "bootstrap" {
  count             = var.enable_https_listener ? 0 : 1
  load_balancer_arn = aws_lb.docs.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "HTTPS certificate validation is pending"
      status_code  = "503"
    }
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

# --- Private access to S3 and Systems Manager ---------------------------------

# No endpoint policy: the default defers to IAM, and the only principal that can
# route through this endpoint is an instance whose role grants s3:GetObject on
# one prefix. A bucket allowlist here would instead have to be kept in step with
# the AWS-managed buckets SSM Agent reads, and silently breaks the agent when it
# is not.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.docs.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.app.id]

  tags = merge(local.common_tags, { Name = "${var.service_name}-s3" })
}

resource "aws_vpc_endpoint" "ssm" {
  vpc_id            = aws_vpc.docs.id
  service_name      = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type = "Interface"

  subnet_ids         = [aws_subnet.app.id]
  security_group_ids = [aws_security_group.endpoints.id]

  # Without this the agent would have to be reconfigured to use the endpoint's
  # own hostname instead of ssm.<region>.amazonaws.com.
  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.service_name}-ssm" })
}

resource "aws_vpc_endpoint" "ssmmessages" {
  vpc_id            = aws_vpc.docs.id
  service_name      = "com.amazonaws.${var.aws_region}.ssmmessages"
  vpc_endpoint_type = "Interface"

  subnet_ids         = [aws_subnet.app.id]
  security_group_ids = [aws_security_group.endpoints.id]

  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.service_name}-ssmmessages" })
}

resource "aws_vpc_endpoint" "ec2messages" {
  vpc_id            = aws_vpc.docs.id
  service_name      = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type = "Interface"

  subnet_ids         = [aws_subnet.app.id]
  security_group_ids = [aws_security_group.endpoints.id]

  private_dns_enabled = true

  tags = merge(local.common_tags, { Name = "${var.service_name}-ec2messages" })
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

resource "aws_instance" "docs" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.docs.name

  # There is no public address and no route to a NAT, so the load balancer and
  # the VPC endpoints are the only ways in and out.
  associate_public_ip_address = false

  # Cloud-init runs user data once, on first boot. Without replacement, editing
  # a templated value here would change the plan and change nothing on the host,
  # which is the wrong failure mode for the OIDC client and the site paths.
  # Replacement regenerates the session secret and empties the release
  # directory, so a redeploy has to follow.
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

  # The agent registers with Systems Manager during first boot. Terraform would
  # otherwise be free to create the instance alongside the endpoints, the
  # security group rules, and the policy attachments the agent needs, leaving a
  # node that never becomes managed until something restarts the agent.
  depends_on = [
    aws_iam_role_policy_attachment.instance_ssm_core,
    aws_iam_role_policy_attachment.instance_releases,
    aws_vpc_endpoint.ssm,
    aws_vpc_endpoint.ssmmessages,
    aws_vpc_endpoint.ec2messages,
    aws_vpc_endpoint.s3,
    aws_vpc_security_group_egress_rule.app_to_endpoints,
    aws_vpc_security_group_egress_rule.app_to_s3,
    aws_vpc_security_group_ingress_rule.endpoints_from_app,
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

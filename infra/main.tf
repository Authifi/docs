provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_ec2_instance_type" "selected" {
  instance_type = var.instance_type
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
  # The deployment job declares `environment: production`, and that changes the
  # token it gets: GitHub's default subject names the *environment* when a job
  # references one, so `repo:<owner>/<name>:ref:refs/heads/main` is not a
  # subject this workflow can ever present. Pinning the ref form fails nothing
  # a plan or a validate can see — it fails the first real deployment with
  # `Not authorized to perform sts:AssumeRoleWithWebIdentity`, after the
  # release archive is already in S3.
  #
  # This is the legacy (mutable) subject format, which is what the repository's
  # OIDC customisation currently returns: `use_default` is true and
  # `use_immutable_subject` is false. Turning immutable subjects on for the
  # org or repository rewrites this claim to the
  # `repo:<owner>@<owner-id>/<name>@<repo-id>:environment:<env>` form, and this
  # value has to be updated in the same change.
  github_repository_subject = "repo:${var.github_repository}:environment:${var.deploy_environment}"
  github_oidc_provider_arn  = coalesce(var.existing_github_oidc_provider_arn, try(aws_iam_openid_connect_provider.github[0].arn, null))
  release_bucket_name       = coalesce(var.release_bucket_name, "${var.service_name}-releases-${data.aws_caller_identity.current.account_id}")

  common_tags = merge(var.tags, {
    ManagedBy  = "Terraform"
    Repository = var.github_repository
    Service    = var.service_name
  })

  # The installer is bash, so it is full of dollar signs, brace expansions, and
  # heredocs of its own. Base64 is what carries it through a JSON document and
  # a shell heredoc without either language touching it.
  #
  # It travels with the command that runs it rather than in user data. Anything
  # in user data is part of the instance's identity under
  # `user_data_replace_on_change`, so while the installer lived there, editing
  # it destroyed and rebuilt the host: a new session secret, every user signed
  # out, an empty release tree, and a redeploy needed before the site answered.
  deploy_script_base64 = base64encode(file("${path.module}/scripts/deploy-release.sh"))

  # Everything Terraform tells the docs server about itself, and the only
  # channel it has. Carried as one JSON document rather than five values
  # interpolated into a file, because that file was loaded with `source`: an
  # accepted `site_dir` containing a space split into an assignment plus a
  # command, and a command substitution or a semicolon in any of these ran as
  # root on the deployment path. Neither consumer evaluates anything now --
  # the installer parses this, and the bootstrap renders systemd's own
  # `EnvironmentFile` from it on the host.
  #
  # Nothing secret belongs here: user data is readable from the instance
  # metadata service by anything that can reach it, and it lands in Terraform
  # state. The session secret is generated on the host for exactly that reason.
  host_config = {
    OIDC_ISSUER      = var.oidc_issuer
    OIDC_CLIENT_ID   = var.oidc_client_id
    PUBLIC_BASE_URL  = var.public_base_url
    SITE_DIR         = var.site_dir
    POST_LOGOUT_PATH = var.post_logout_path
  }

  user_data = templatefile("${path.module}/templates/user-data.sh.tftpl", {
    config_json = jsonencode(local.host_config)
    app_port    = var.app_port
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

# The route table the subnet is actually associated with, read because this root
# no longer owns it. Whether the shared NAT route exists is now an assumption
# about someone else's Terraform, and the instance below turns it into a check:
# a subnet that is merely not public is indistinguishable, until first boot,
# from one with no way out at all.
data "aws_route_table" "app" {
  subnet_id = var.private_app_subnet_id
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

  # This load balancer owns the name users reach the docs through and serves the
  # certificate that name is covered by. Deleting it is a DNS-visible outage
  # that one misdirected `terraform destroy` or stray `-target` can cause, and
  # nothing about it is recoverable in place: the replacement has a different
  # DNS name, so the external record has to be moved again.
  #
  # Ordinary applies are unaffected. Teardown is one apply longer: set
  # `enable_alb_deletion_protection = false`, apply, then destroy.
  enable_deletion_protection = var.enable_alb_deletion_protection

  # Access logs are deliberately absent. An access log of this load balancer is
  # a per-request record of which protected page each session read, which is a
  # more sensitive artifact than the documentation itself and would need a
  # bucket, a region-specific delivery policy, and a retention decision of its
  # own. The questions access logs usually answer here — is the target healthy,
  # how many 5xx — are already answered by target health and load balancer
  # metrics, and the application's own log goes to the journal on the host.

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

    # The host advertises `public_base_url` as its OIDC redirect origin, and the
    # load balancer serves only this certificate's name. A mismatch is a sign-in
    # loop whose first symptom is Authifi rejecting the redirect URI, which
    # points at the identity provider rather than at these two variables.
    precondition {
      condition     = try(regex("^https://([^/?#]+)", var.public_base_url)[0], "") == var.custom_domain_name
      error_message = "public_base_url's host must equal custom_domain_name; the load balancer serves no other name."
    }
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

  # On the flip, this listener stops answering and starts redirecting to 443.
  # Without the ordering Terraform is free to make that change before creating
  # the listener on 443, leaving a window where every request is redirected to
  # a closed port — worse than the holding response it replaced.
  depends_on = [aws_lb_listener.https]
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
  bucket        = local.release_bucket_name
  force_destroy = var.release_bucket_force_destroy

  tags = merge(local.common_tags, { Name = local.release_bucket_name })
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket = aws_s3_bucket.releases.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# The public access block stops an ACL from making an object public. It says
# nothing about one granting a named account or the authenticated-users group,
# and an object ACL is an access decision made outside the two IAM policies in
# this file. This removes the mechanism rather than constraining it.
resource "aws_s3_bucket_ownership_controls" "releases" {
  bucket = aws_s3_bucket.releases.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# S3 answers plain HTTP, and nothing in an IAM policy or an endpoint URL stops
# a client from using it. What travels through this bucket is the code this host
# runs, so a request carrying one in the clear is both readable and modifiable
# in flight, and the bucket is the only place that can refuse it for every
# caller — including ones whose IAM policy this root does not own.
#
# The condition is what makes a `Deny` on `s3:*` with a wildcard principal safe
# to write: without it, the same statement locks the account out of its own
# bucket. A deny-only policy is also not a public policy, so it does not
# collide with `block_public_policy`.
data "aws_iam_policy_document" "releases_bucket" {
  statement {
    sid    = "DenyRequestsNotOverTLS"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    # Both ARNs: bucket-level operations are authorised against the bucket, and
    # object-level ones against the keys, so naming one leaves the other
    # reachable in the clear.
    resources = [aws_s3_bucket.releases.arn, "${aws_s3_bucket.releases.arn}/*"]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "releases" {
  bucket = aws_s3_bucket.releases.id
  policy = data.aws_iam_policy_document.releases_bucket.json

  # PutBucketPolicy is evaluated against the public access block, so the two
  # are ordered rather than left to race on a first apply.
  depends_on = [aws_s3_bucket_public_access_block.releases]
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

    # Not public is not the same as reachable. Both halves are checked: a
    # default route to an internet gateway carries no nat_gateway_id, and a NAT
    # route for a narrower prefix is not a default route.
    precondition {
      condition     = anytrue([for route in data.aws_route_table.app.routes : route.cidr_block == "0.0.0.0/0" && route.nat_gateway_id != ""])
      error_message = "private_app_subnet_id's route table must send 0.0.0.0/0 to the shared NAT Gateway; the docs server needs that egress for OIDC discovery, the authorization-code exchange, Systems Manager, and first-boot package installation."
    }

    # Family-name regex misses oddball types and new families. The EC2 API's
    # `supported_architectures` is the contract the Ubuntu AMI filter and the
    # Linux wheelhouse depend on.
    precondition {
      condition     = contains(data.aws_ec2_instance_type.selected.supported_architectures, "x86_64")
      error_message = "instance_type must be x86_64: the AMI and Linux wheelhouse are amd64 only."
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
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.${data.aws_partition.current.dns_suffix}/releases/{{ ReleaseSha }}.tar.gz"
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
            path = "https://${aws_s3_bucket.releases.id}.s3.${var.aws_region}.${data.aws_partition.current.dns_suffix}/releases/{{ ReleaseSha }}.tar.gz.sha256"
          })
          destinationPath = "/opt/authifi-docs/incoming/{{ ReleaseSha }}/"
        }
      },
      # The installer is delivered here, immediately before it runs, so that
      # editing it produces a new document version rather than replacing the
      # instance. The provider promotes that version: on a content change it
      # calls UpdateDocument and then UpdateDocumentDefaultVersion with the
      # version returned, and SendCommand naming no version resolves the
      # default. Schema 2.2 is what allows the in-place update at all.
      #
      # The agent hardcodes `sh` and hands it the assembled script, so this runs
      # under dash on Ubuntu and the shebang is never consulted: no `pipefail`,
      # no `[[`, no here-strings. `set -eu` is also what makes a failed decode
      # fail the step, since the agent otherwise reports only the last command's
      # status. The installer's own shebang still selects bash for the installer.
      {
        action = "aws:runShellScript"
        name   = "installRelease"
        inputs = {
          runCommand = [
            "set -eu",
            # /run is root-owned tmpfs; /tmp is world-writable.
            "install -d -m 0700 -o root -g root /run/authifi-docs",
            "installer=\"$(mktemp /run/authifi-docs/deploy-release.XXXXXXXX)\"",
            "trap 'rm -f \"$installer\"' EXIT HUP INT TERM",
            "base64 -d > \"$installer\" <<'AUTHIFI_DOCS_INSTALLER'",
            local.deploy_script_base64,
            "AUTHIFI_DOCS_INSTALLER",
            "chmod 0700 \"$installer\"",
            "\"$installer\" '{{ ReleaseSha }}'",
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

    # StringEquals throughout, never StringLike: a pattern on any of these
    # claims turns an exact binding into one that anything matching satisfies.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.github_repository_subject]
    }

    # The subject is one string, and everything in it is a name someone can
    # take. These three bind the same run through claims the subject either
    # does not carry or carries only by convention.
    #
    # `environment` is stated separately from the subject deliberately: it is
    # the one claim that does not move if GitHub's subject format changes.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:environment"
      values   = [var.deploy_environment]
    }

    # An environment-scoped subject says nothing about the branch. Without
    # this, a `production` deployment job on any branch presents the same
    # subject as one on the release branch.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:ref"
      values   = ["refs/heads/${var.deploy_branch}"]
    }

    # Numeric repository IDs are never reused, so this is what a repository
    # deleted and recreated at `owner/name`, or renamed into that path, cannot
    # inherit.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:repository_id"
      values   = [var.github_repository_id]
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

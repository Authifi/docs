# Deploy a Minimal AWS Web App with Authifi

This guide deploys a small web application on AWS and protects it with Authifi. The reference path is:

```text
Internet → DNS provider → HTTPS ALB → private EC2 instance → systemd service
                                           ↓
                                   Authifi over HTTPS
```

It uses one application instance. Reuse existing networking when it meets the requirements below, or create the minimal network described in this guide. Choose a more available architecture when the cost of downtime justifies it.

## Before you start

You need:

- an Authifi tenant administrator;
- an AWS account and AWS CLI credentials;
- a DNS name managed by Route 53 or another DNS provider; and
- a Linux application release containing executable `run.sh` and `install-release.sh` files.

The application must listen on `0.0.0.0:8000` and return HTTP 200 from `/health`.

!!! important
    A security group allows traffic; it does not create a network route. A private instance needs a NAT gateway, NAT instance, or another approved egress path to reach Authifi. Server-side OIDC uses outbound HTTPS for discovery, code exchange, UserInfo, and logout. A token-validating API also needs reliable JWKS refresh.

## Choose the authentication pattern

### Server-side web application

Use this pattern for a traditional Python, Node, Java, .NET, or similar web server:

1. The server creates cryptographically random `state`, `nonce`, and PKCE values.
2. It redirects the browser to Authifi.
3. Authifi redirects the browser to the application's public callback.
4. The server validates `state`, exchanges the code, and validates the ID token.
5. The server stores only the minimum user identity and authentication time in a signed, `Secure`, `HttpOnly`, `SameSite=Lax` session cookie.

Register this as an Authifi **web/confidential** client. A confidential client normally authenticates at the token endpoint with a client secret. PKCE can supplement that authentication, but it does not turn a confidential client into a public client.

The callback and logout handlers must be public. Protect application content only after those routes have run.

### Browser SPA plus API

Use this pattern when a browser application calls a separate API:

1. The browser uses authorization code flow with S256 PKCE.
2. Register the SPA as an Authifi **public** client; do not issue or embed a client secret.
3. Keep the callback route outside the SPA's authentication guard.
4. Send the access token to the API as `Authorization: Bearer <token>`.
5. The API validates the JWT signature, exact issuer, audience, expiry, and required permission scopes.
6. Return 401 for a missing or invalid token and 403 for a valid token without the required permission.

Frontend route and button checks improve usability but do not replace API authorization.

## Register the Authifi client

In **SSO Integration → App Dashboard**, create the appropriate client described above. Configure:

- the Authifi authority or issuer URL;
- a generated client ID;
- `authorization_code` grant type;
- `code` response type;
- exact production callback, such as `https://app.example.com/auth/callback`;
- exact post-logout URL, such as `https://app.example.com/`;
- `openid profile email` scopes;
- S256 PKCE for public browser clients; and
- only the identity providers and user groups that should access the application.

If the application calls a protected API, also create or select its resource server identifier. Send Authifi's `resource=<RESOURCE_SERVER_IDENTIFIER>` parameter on both the authorization request and token request. Enforce the resulting permission values from the access token's space-separated `scope` claim.

See [SSO Integration](sso-integration-guide.md) for client, provider, group, role, and permission configuration.

Keep these values:

```dotenv
AUTHIFI_AUTHORITY=https://auth.example.com/_api/auth/your-tenant
AUTHIFI_CLIENT_ID=replace-with-client-id
AUTHIFI_RESOURCE=https://app.example.com/api
APP_BASE_URL=https://app.example.com
AUTHIFI_CALLBACK_URL=https://app.example.com/auth/callback
AUTHIFI_POST_LOGOUT_URL=https://app.example.com/
```

For a server-side client, also keep `AUTHIFI_CLIENT_SECRET` and a random `SESSION_SECRET`. Do not put either value in source control, a release archive, Terraform variables, or Terraform state.

## EC2 or Fargate?

Use one EC2 instance when the application is low volume, brief deployment downtime is acceptable, and the team is comfortable patching one host. It has the smallest application runtime model, but the team owns operating-system maintenance.

Use Fargate when the team already deploys containers or wants AWS to manage hosts. The network, ALB, ACM, Authifi, health-check, and egress requirements stay the same, but ECR, a task definition, an ECS service, execution roles, and container deployment become part of the system.

For either choice, the ALB is a fixed cost driver. Private-subnet egress, logs, artifact storage, and compute are additional cost drivers. Check current AWS pricing in the target Region before creating resources.

## Configure the AWS infrastructure

You can configure these resources in the AWS Console, with your organization's existing infrastructure tooling, or with the optional Terraform appendix.

### Reuse or create the network

Reuse a VPC when it already provides:

- DNS resolution and DNS hostnames;
- two public subnets in different Availability Zones for the ALB;
- a private application subnet for EC2;
- a route from each public subnet to an internet gateway; and
- outbound DNS and HTTPS from the private subnet.

If those resources do not exist, create them:

1. Create a VPC, for example `10.0.0.0/16`, with DNS resolution and DNS hostnames enabled.
2. Create two public subnets in different Availability Zones, for example `10.0.0.0/24` and `10.0.1.0/24`.
3. Create a private application subnet, for example `10.0.10.0/24`.
4. Attach an internet gateway to the VPC.
5. Create a public route table with `0.0.0.0/0` targeting the internet gateway, and associate both public subnets with it.
6. Allocate an Elastic IP and create a NAT gateway in one public subnet.
7. Create a private route table with `0.0.0.0/0` targeting the NAT gateway, and associate the private application subnet with it.

One NAT gateway is the smaller, lower-cost starting point, but it creates an Availability Zone dependency and can add cross-zone data charges. For higher availability, use a private application subnet and NAT gateway in each Availability Zone and keep each subnet's egress in-zone.

VPC endpoints can keep S3 and Systems Manager traffic on the AWS network, but they do not provide access to Authifi's public OIDC endpoints. The application still needs an approved internet egress path. Keep the default network ACLs unless your organization has a specific ACL policy; use security groups as the primary workload controls.

### Create the security groups

Create an ALB security group:

- allow inbound TCP 443 from the intended clients;
- optionally allow TCP 80 only to redirect HTTP to HTTPS; and
- allow outbound TCP 8000 only to the application security group.

Create an application security group:

- allow inbound TCP 8000 only from the ALB security group; and
- allow outbound TCP 443 for Authifi, S3, Systems Manager, and package or key retrieval.

The reference assumes Amazon-provided VPC DNS, whose resolver traffic is not filtered by security groups. If you use custom DNS resolvers, allow DNS to their specific addresses. Do not open SSH; use Systems Manager Session Manager and Run Command.

### Create the instance role and release bucket

Create a private S3 bucket for release archives:

- block all public access;
- enable versioning;
- enable default encryption; and
- deny requests that do not use TLS.

Create an EC2 IAM role and instance profile. Attach `AmazonSSMManagedInstanceCore`, then add only:

- `s3:GetObject` on `<release-bucket-arn>/releases/*`; and
- `ssm:GetParameter` on the runtime configuration parameter.

If the runtime parameter uses a customer-managed KMS key, also allow `kms:Decrypt` for that key.

### Launch the EC2 instance

Launch the current Amazon Linux 2023 AMI with:

- a small instance type such as `t3.micro`;
- the private application subnet;
- no public IP and no SSH key pair;
- the application security group and instance profile;
- an encrypted `gp3` root volume; and
- instance metadata service v2 required.

Save the following as `user-data.sh` and supply it as instance user data:

```bash
#!/usr/bin/env bash
set -euxo pipefail

app_name="minimal-web-app"

useradd --system \
  --home-dir "/var/lib/${app_name}" \
  --create-home \
  --shell /sbin/nologin \
  "$app_name"

install -d -m 0755 -o root -g root "/opt/${app_name}/releases"
install -d -m 0700 -o root -g root "/etc/${app_name}"

cat >"/etc/systemd/system/${app_name}.service" <<'UNIT'
[Unit]
Description=Minimal Authifi web application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=minimal-web-app
Group=minimal-web-app
WorkingDirectory=/opt/minimal-web-app/current
EnvironmentFile=/etc/minimal-web-app/runtime.env
ExecStart=/opt/minimal-web-app/current/run.sh
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/minimal-web-app

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${app_name}.service"
```

Amazon Linux 2023 AWS AMIs include the Systems Manager Agent. Confirm the instance appears as a managed node before deployment.

### Create the ALB, TLS certificate, and DNS record

1. Request an ACM public certificate for a subdomain such as `app.example.com` in the same Region as the ALB.
2. ACM provides one or more DNS validation CNAME records. Add them at your DNS provider and wait for the certificate status to become **Issued**.
3. Create an internet-facing Application Load Balancer in the two public subnets with the ALB security group.
4. Create an instance target group on HTTP port 8000 with `/health` as its health check.
5. Register the EC2 instance in the target group.
6. Add an HTTPS 443 listener using the ACM certificate and forward to the target group.
7. Optionally add an HTTP 80 listener that redirects to HTTPS.
8. Create the application DNS record:
   - in Route 53, use an alias A record (and alias AAAA when using IPv6) to the ALB;
   - at another provider, use a CNAME from the application subdomain to the ALB hostname.

Use a subdomain for the simplest portable configuration. A zone apex needs provider-specific ALIAS, ANAME, or CNAME-flattening support. The AWS-owned `*.elb.amazonaws.com` hostname is not a production substitute because you cannot obtain an ACM certificate for it.

Record the EC2 instance ID, release bucket name, application hostname, ACM certificate ARN, VPC ID, public subnet IDs, and private subnet ID. The deployment commands and optional Terraform configuration use these values.

## Store runtime configuration

For the server-side pattern, generate a session secret and create `runtime.env` locally:

```bash
SESSION_SECRET="$(openssl rand -base64 48)"

cat >runtime.env <<'EOF'
AUTHIFI_AUTHORITY=https://auth.example.com/_api/auth/your-tenant
AUTHIFI_CLIENT_ID=replace-with-client-id
AUTHIFI_CLIENT_SECRET=replace-with-client-secret
AUTHIFI_RESOURCE=https://app.example.com/api
AUTHIFI_CALLBACK_URL=https://app.example.com/auth/callback
AUTHIFI_POST_LOGOUT_URL=https://app.example.com/
APP_BASE_URL=https://app.example.com
EOF
printf 'SESSION_SECRET=%s\n' "$SESSION_SECRET" >>runtime.env

unset SESSION_SECRET
```

Put the complete environment file in Parameter Store as one `SecureString`:

```bash
aws ssm put-parameter \
  --name /minimal-web-app/runtime-env \
  --type SecureString \
  --overwrite \
  --value "$(cat runtime.env)"

rm -f runtime.env
```

If you encrypt the parameter with a customer-managed KMS key, add narrowly scoped `kms:Decrypt` permission for that key to the instance role.

For a SPA plus API, public browser settings can be supplied at build time, but never place a client secret in browser code. Store API issuer, audience, and any server secrets through the same runtime mechanism.

## Define the release contract

The release archive must contain:

```text
run.sh
install-release.sh
application files and production dependencies
```

`run.sh` starts the application in the foreground. For example:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

Build Linux-compatible dependencies before creating the archive. Do not copy a macOS or Windows virtual environment to Linux.

Save this generic installer as `install-release.sh` in the release:

```bash
#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release ID required}"
runtime_parameter="${2:?runtime parameter required}"
app_port="${3:-8000}"
app_name="minimal-web-app"
source_dir="$(cd "$(dirname "$0")" && pwd)"
app_root="/opt/${app_name}"
release_dir="${app_root}/releases/${release_id}"
current="${app_root}/current"
previous="$(readlink -f "$current" 2>/dev/null || true)"
env_tmp="$(mktemp)"
link_tmp="${app_root}/.current-${release_id}"
staging=""

cleanup() {
  rm -f "$env_tmp" "$link_tmp"
  if [[ -n "$staging" ]]; then
    rm -rf "$staging"
  fi
}
trap cleanup EXIT

case "$release_id" in
  (*[!A-Za-z0-9._-]*|'') echo "invalid release ID" >&2; exit 2 ;;
esac

test -x "${source_dir}/run.sh"
if [[ ! -e "$release_dir" ]]; then
  staging="$(mktemp -d "${app_root}/releases/.staging-${release_id}.XXXXXX")"
  cp -a "${source_dir}/." "$staging/"
  chown -R root:root "$staging"
  chmod -R a+rX,go-w "$staging"
  mv "$staging" "$release_dir"
  staging=""
elif [[ ! -d "$release_dir" || -L "$release_dir" ]]; then
  echo "invalid existing release path" >&2
  exit 1
fi

aws ssm get-parameter \
  --name "$runtime_parameter" \
  --with-decryption \
  --query Parameter.Value \
  --output text >"$env_tmp"
test -s "$env_tmp"
install -m 0600 -o root -g root "$env_tmp" "/etc/${app_name}/runtime.env"

ln -s "$release_dir" "$link_tmp"
mv -Tf "$link_tmp" "$current"

if ! systemctl restart "${app_name}.service" ||
   ! curl --fail --silent --show-error \
      --connect-timeout 2 --max-time 5 \
      --retry 12 --retry-delay 2 --retry-connrefused \
      "http://127.0.0.1:${app_port}/health" >/dev/null; then
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -s "$previous" "$link_tmp"
    mv -Tf "$link_tmp" "$current"
    systemctl restart "${app_name}.service" || true
  else
    rm -f "$current"
    systemctl stop "${app_name}.service" || true
  fi
  echo "release failed; previous release restored" >&2
  exit 1
fi
```

Make both scripts executable before packaging:

```bash
chmod 0755 release/run.sh release/install-release.sh
RELEASE_ID="$(git rev-parse --short=12 HEAD)"
tar -C release -czf "${RELEASE_ID}.tgz" .
```

## Deploy through S3 and Systems Manager

Upload the immutable release:

```bash
BUCKET="replace-with-release-bucket"
INSTANCE_ID="replace-with-ec2-instance-id"
RUNTIME_PARAMETER="/minimal-web-app/runtime-env"

if aws s3api head-object \
  --bucket "$BUCKET" \
  --key "releases/${RELEASE_ID}.tgz" >/dev/null 2>&1; then
  echo "Reusing existing immutable release ${RELEASE_ID}"
else
  aws s3api put-object \
    --bucket "$BUCKET" \
    --key "releases/${RELEASE_ID}.tgz" \
    --body "${RELEASE_ID}.tgz" \
    --if-none-match "*" >/dev/null ||
    aws s3api head-object \
      --bucket "$BUCKET" \
      --key "releases/${RELEASE_ID}.tgz" >/dev/null
fi
```

Create the Run Command parameters:

```bash
cat >deploy-parameters.json <<EOF
{
  "commands": [
    "set -euo pipefail",
    "WORK_DIR=\"\$(mktemp -d /opt/minimal-web-app/.deploy.XXXXXX)\"",
    "trap 'rm -rf \"\$WORK_DIR\"' EXIT",
    "aws s3 cp 's3://${BUCKET}/releases/${RELEASE_ID}.tgz' \"\$WORK_DIR/release.tgz\" --only-show-errors",
    "mkdir \"\$WORK_DIR/release\" && tar -xzf \"\$WORK_DIR/release.tgz\" -C \"\$WORK_DIR/release\"",
    "bash \"\$WORK_DIR/release/install-release.sh\" '${RELEASE_ID}' '${RUNTIME_PARAMETER}' '8000'"
  ]
}
EOF
```

Send the command and wait for it:

```bash
COMMAND_ID="$(
  aws ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name AWS-RunShellScript \
    --comment "Deploy ${RELEASE_ID}" \
    --parameters file://deploy-parameters.json \
    --query Command.CommandId \
    --output text
)"

STATUS=""
for attempt in $(seq 1 60); do
  STATUS="$(
    aws ssm get-command-invocation \
      --command-id "$COMMAND_ID" \
      --instance-id "$INSTANCE_ID" \
      --query Status \
      --output text 2>/dev/null || true
  )"

  case "$STATUS" in
    Success|Cancelled|Failed|TimedOut) break ;;
  esac
  sleep 5
done

aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}'

rm -f deploy-parameters.json
test "$STATUS" = "Success"
```

To roll back, set `RELEASE_ID` to a previously uploaded release and repeat the deployment. The installer keeps each release in its own directory and atomically changes the `current` symlink.

## Verify the deployment

Keep verification bounded to these seven checks:

1. `curl -fsS https://app.example.com/health` returns HTTP 200.
2. A protected page redirects an unauthenticated browser to the expected Authifi authority.
3. Authifi returns to the exact registered callback and the application establishes a session or accepts the access token.
4. An authenticated, authorized user can load protected content.
5. A user without the required group or permission receives the intended denial; an API returns 403.
6. Logout clears local state, reaches Authifi's end-session endpoint, and returns to the registered post-logout URL.
7. Redeploying a previous release restores service and passes `/health`.

Also confirm that the EC2 instance has no public IP, port 8000 is reachable only from the ALB security group, and application logs do not contain tokens or secrets.

## Troubleshooting

**The instance is not managed by Systems Manager**

- Confirm `AmazonSSMManagedInstanceCore` is attached.
- Confirm the SSM Agent is running.
- Confirm the private subnet can reach Systems Manager endpoints over HTTPS.

**The ALB target stays unhealthy**

- Call `/health` through an SSM session from the instance.
- Confirm the process listens on `0.0.0.0:8000`, not only `127.0.0.1`.
- Check `journalctl -u minimal-web-app.service`.

**Login fails before the callback**

- Compare the requested redirect URI character-for-character with the Authifi registration.
- Confirm the authority is the tenant authority, not the documentation domain.
- Confirm the client type matches the server-side or SPA pattern.

**The callback or token exchange fails**

- Confirm the callback route is public.
- For a confidential client, confirm client authentication is configured.
- For a public client, confirm the original PKCE verifier is available and S256 is required.
- Confirm `resource` is sent on authorization and token requests when requesting an API token.

**Discovery, UserInfo, JWKS, or logout times out**

- Check the private subnet route table; security-group egress alone is insufficient.
- Confirm DNS and HTTPS egress.
- Confirm the Authifi hostname and certificate are valid.

## Appendix: optional Terraform

Use this appendix when you prefer Terraform for the application resources. It reuses the VPC and subnets created or selected in the main procedure, and it accepts an already issued ACM certificate from any DNS provider.

Create an empty directory, save the user-data script from [Launch the EC2 instance](#launch-the-ec2-instance) as `user-data.sh`, and save the following as `main.tf`. Keep the default application name unless you also update the service and installer scripts.

The reference assumes the VPC uses Amazon-provided DNS, whose resolver traffic is not filtered by security groups. If the application uses custom DNS resolvers, add explicit egress rules for their addresses.

```hcl
terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type = string
}

variable "name" {
  type    = string
  default = "minimal-web-app"
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)

  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "Provide at least two public subnets in different Availability Zones."
  }
}

variable "private_subnet_id" {
  type = string
}

variable "certificate_arn" {
  type = string
}

variable "release_bucket_name" {
  type = string
}

variable "runtime_parameter_name" {
  type    = string
  default = "/minimal-web-app/runtime-env"
}

variable "app_port" {
  type    = number
  default = 8000
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  description = "Public HTTPS ingress"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_security_group" "app" {
  name_prefix = "${var.name}-app-"
  description = "Application traffic from the ALB"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.app.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.app_port
  to_port                      = var.app_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "app_https" {
  security_group_id = aws_security_group.app.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_lb" "app" {
  name               = var.name
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "app" {
  name        = var.name
  vpc_id      = var.vpc_id
  port        = var.app_port
  protocol    = "HTTP"
  target_type = "instance"

  health_check {
    path                = "/health"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_s3_bucket" "releases" {
  bucket = var.release_bucket_name
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket                  = aws_s3_bucket.releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "releases" {
  bucket = aws_s3_bucket.releases.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "releases_tls_only" {
  statement {
    effect = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.releases.arn,
      "${aws_s3_bucket.releases.arn}/*"
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "releases_tls_only" {
  bucket = aws_s3_bucket.releases.id
  policy = data.aws_iam_policy_document.releases_tls_only.json
}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name_prefix        = "${var.name}-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "runtime" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }

  statement {
    actions = ["ssm:GetParameter"]
    resources = [
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${var.runtime_parameter_name}"
    ]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "${var.name}-runtime"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_iam_instance_profile" "app" {
  name_prefix = "${var.name}-"
  role        = aws_iam_role.app.name
}

resource "aws_instance" "app" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = "t3.micro"
  subnet_id                   = var.private_subnet_id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.app.name

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    encrypted   = true
    volume_size = 12
    volume_type = "gp3"
  }

  user_data = file("${path.module}/user-data.sh")

  tags = {
    Name = var.name
  }
}

resource "aws_lb_target_group_attachment" "app" {
  target_group_arn = aws_lb_target_group.app.arn
  target_id        = aws_instance.app.id
  port             = var.app_port
}

output "instance_id" {
  value = aws_instance.app.id
}

output "release_bucket" {
  value = aws_s3_bucket.releases.id
}

output "alb_dns_name" {
  value = aws_lb.app.dns_name
}
```

Create `terraform.tfvars`:

```hcl
aws_region          = "us-east-1"
vpc_id              = "vpc-0123456789abcdef0"
public_subnet_ids   = ["subnet-public-a", "subnet-public-b"]
private_subnet_id   = "subnet-private-app"
certificate_arn     = "arn:aws:acm:us-east-1:123456789012:certificate/replace-me"
release_bucket_name = "replace-with-a-globally-unique-release-bucket"
```

Initialize and review:

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=tfplan
terraform apply tfplan
```

Use a remote encrypted Terraform backend for shared or production infrastructure. The local backend is acceptable only for an individual tutorial run where losing state is acceptable.

### Optional: manage ACM and Route 53 with Terraform

If the application's public DNS zone is in Route 53, Terraform can request and validate the certificate and create the application record. Add the following resources:

```hcl
variable "domain_name" {
  type = string
}

variable "hosted_zone_id" {
  type = string
}

resource "aws_acm_certificate" "app" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.app.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      record = option.resource_record_value
      type   = option.resource_record_type
    }
  }

  allow_overwrite = true
  zone_id         = var.hosted_zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn = aws_acm_certificate.app.arn
  validation_record_fqdns = [
    for record in aws_route53_record.certificate_validation : record.fqdn
  ]
}

resource "aws_route53_record" "app" {
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.app.dns_name
    zone_id                = aws_lb.app.zone_id
    evaluate_target_health = true
  }
}
```

Then replace the HTTPS listener's `certificate_arn` with:

```hcl
certificate_arn = aws_acm_certificate_validation.app.certificate_arn
```

Remove `certificate_arn` from `terraform.tfvars`, add `domain_name` and `hosted_zone_id`, and apply again. When DNS is hosted elsewhere, keep the core configuration and manage the certificate-validation and application records at that provider.


## References

- [Authifi SSO Integration](sso-integration-guide.md)
- [Terraform AWS provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
- [AWS Parameter Store SecureString parameters](https://docs.aws.amazon.com/systems-manager/latest/userguide/secure-string-parameter-kms-encryption.html)
- [Application Load Balancer security groups](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-update-security-groups.html)
- [AWS Fargate](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate.html)

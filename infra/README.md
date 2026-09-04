# AWS Infrastructure

This directory provisions the AWS deployment path for `Authifi/docs`:

- a private ECR repository for immutable image pushes
- an AWS App Runner service for the Starlette docs server
- IAM roles for App Runner runtime access and GitHub Actions OIDC deployment
- an optional App Runner custom domain association for `docs.authifi.io`

The Terraform root intentionally does **not** configure a backend. Choose the state backend at init time so each caller can supply their own S3, local, or Terraform Cloud settings.

## Files

- `versions.tf`: Terraform and provider version constraints
- `variables.tf`: caller-supplied inputs
- `main.tf`: ECR, IAM, App Runner, and custom-domain resources
- `outputs.tf`: deployment and DNS outputs
- `terraform.tfvars.example`: starter variable file with placeholder ARNs only

## Prerequisites

- Terraform `>= 1.6`
- AWS credentials with permission to create ECR, IAM, and App Runner resources
- Two pre-created AWS Secrets Manager secrets:
  - one for the OIDC client secret
  - one for the Starlette session secret

Terraform accepts **secret ARNs only**. Secret values stay in Secrets Manager and are not stored in Terraform state.

## Variables

Start from the checked-in example:

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Example contents with placeholder values only:

```hcl
aws_region          = "us-east-1"
service_name        = "authifi-docs"
ecr_repository_name = "authifi-docs"
ecr_image_retention_count = 10
create_service      = false
image_identifier    = ""

oidc_issuer            = "https://issuer.example.com"
oidc_client_id         = "authifi-docs"
oidc_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:authifi/docs/oidc-client-secret-AbCdEf"
session_secret_arn     = "arn:aws:secretsmanager:us-east-1:123456789012:secret:authifi/docs/session-secret-ZyXwVu"
runtime_secret_kms_key_arns = []

public_base_url    = "https://docs.authifi.io"
site_dir           = "/app/site"
post_logout_path   = "/privacy-policy/"
custom_domain_name = "docs.authifi.io"

existing_github_oidc_provider_arn = null

tags = {
  Environment = "prod"
  Project     = "authifi-docs"
}
```

## Init

Local state:

```bash
terraform -chdir=infra init
```

S3 backend supplied by the caller:

```bash
terraform -chdir=infra init \
  -backend-config="bucket=my-tf-state" \
  -backend-config="key=authifi/docs/prod.tfstate" \
  -backend-config="region=us-east-1"
```

## Plan

```bash
terraform -chdir=infra plan -var-file=terraform.tfvars
```

## Bootstrap

There is an intentional two-stage bootstrap so the first apply can create ECR before the App Runner service references a real image.

### Stage 1: create ECR, IAM, and optional GitHub OIDC provider

```bash
terraform -chdir=infra apply \
  -var-file=terraform.tfvars \
  -var='create_service=false'
```

Capture the outputs you need for GitHub:

```bash
terraform -chdir=infra output -raw ecr_repository_url
terraform -chdir=infra output -raw github_deploy_role_arn
```

### Stage 2: build and push the first image

Build from the repository root, tagging with an immutable commit SHA. App Runner only runs `linux/amd64`, so the platform must be explicit — an image built on an Apple Silicon or other arm64 workstation will fail to start otherwise:

```bash
AWS_REGION=us-east-1
ECR_REPOSITORY_URL="$(terraform -chdir=infra output -raw ecr_repository_url)"
IMAGE_TAG="$(git rev-parse HEAD)"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$(printf '%s' "$ECR_REPOSITORY_URL" | cut -d/ -f1)"

docker buildx build \
  --platform linux/amd64 \
  --provenance false \
  -t "$ECR_REPOSITORY_URL:$IMAGE_TAG" \
  --push \
  .
```

Confirm the pushed architecture before continuing:

```bash
docker buildx imagetools inspect "$ECR_REPOSITORY_URL:$IMAGE_TAG"
```

The repository is configured with `IMMUTABLE` tags, so a given SHA can only be pushed once. The deploy workflow detects an already-pushed SHA and continues straight to the App Runner update, which makes workflow reruns safe.

### Stage 3: create App Runner and domain association

```bash
IMAGE_IDENTIFIER="$(terraform -chdir=infra output -raw ecr_repository_url):$(git rev-parse HEAD)"

terraform -chdir=infra apply \
  -var-file=terraform.tfvars \
  -var='create_service=true' \
  -var="image_identifier=$IMAGE_IDENTIFIER"
```

## Who Owns The Deployed Image

`var.image_identifier` seeds the App Runner service **at creation time only**. After that, GitHub Actions owns the running image, so `aws_apprunner_service.docs` declares:

```hcl
lifecycle {
  ignore_changes = [
    source_configuration[0].image_repository[0].image_identifier,
  ]
}
```

Without that, the next `terraform apply` after any deploy would see the stale SHA still recorded in `terraform.tfvars` and quietly roll production backwards.

Two consequences to keep in mind:

- `terraform plan` will not show image drift, and `terraform apply` will never change the deployed image. This is intentional.
- Changing the image is an App Runner operation, not a Terraform operation. Use the procedure in [Rollback](#rollback) for both forward and backward moves outside the normal deploy workflow.

If you ever genuinely need Terraform to take the image back, temporarily remove the `ignore_changes` entry in `main.tf`, apply with the desired `image_identifier`, then restore it in the same change. Do not leave it removed.

## Apply After Bootstrap

Routine applies pick up everything except the deployed image:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars
```

The GitHub deployment workflow updates the running service to each new commit SHA without long-lived AWS keys.

## GitHub Actions OIDC

The deploy role trusts **only** the `Authifi/docs` `main` branch:

- audience: `sts.amazonaws.com`
- subject: `repo:Authifi/docs:ref:refs/heads/main`

Even during `create_service=false` bootstrap, the deploy policy is scoped to the predictable App Runner ARN pattern for this AWS account, region, and `service_name` instead of falling back to a global `*` resource.

If the AWS account already has a shared GitHub OIDC provider, set `existing_github_oidc_provider_arn`. Otherwise this module creates the account-level provider for `https://token.actions.githubusercontent.com`.

Recommended GitHub repository variables after bootstrap:

- `AWS_REGION`
- `AWS_DEPLOY_ROLE_ARN`
- `APP_RUNNER_SERVICE_ARN`
- `ECR_REPOSITORY_URL`

Populate them from Terraform outputs:

```bash
terraform -chdir=infra output -raw aws_region
terraform -chdir=infra output -raw github_deploy_role_arn
terraform -chdir=infra output -raw apprunner_service_arn
terraform -chdir=infra output -raw ecr_repository_url
```

One optional variable, `APP_RUNNER_SERVICE_URL`, controls where the deploy
workflow's post-deploy check points. After waiting for the App Runner operation,
the workflow requests `/privacy-policy/` from the live origin and requires HTTP
200 with a `text/html` content type, so a container that starts but cannot serve
fails the deploy rather than the next visitor.

Leave the variable unset and the check uses the App Runner hostname from
`describe-service`, which always exists and needs no configuration. Set it once
DNS is cut over to verify the origin real users reach:

```bash
terraform -chdir=infra output -raw apprunner_service_https_url  # App Runner hostname
# or, after cutover, the custom domain:
terraform -chdir=infra output -raw custom_domain_cname_record
```

## Runtime Configuration

Plain App Runner environment variables, all set from Terraform variables of the
same lowercase name:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `PUBLIC_BASE_URL`
- `SITE_DIR`
- `POST_LOGOUT_PATH`

`post_logout_path` defaults to `/privacy-policy/` and is validated at plan time:
it must be site-relative, free of backslashes and control characters, and one of
the paths the server actually serves publicly. A protected path would send every
logged-out user straight back into a login redirect, so Terraform rejects it
rather than letting it reach production. Whatever value you choose must also be
registered with Authifi as a post-logout redirect URI, because the server sends
it as `post_logout_redirect_uri` during RP-initiated logout.

If you change `post_logout_path` after the service exists, a normal
`terraform apply` picks it up — the `ignore_changes` lifecycle rule covers only
`image_identifier`, not the environment variables.

Secrets injected from Secrets Manager by ARN:

- `OIDC_CLIENT_SECRET`
- `SESSION_SECRET`

The App Runner instance role is scoped to `secretsmanager:GetSecretValue` on only those two ARNs. If either secret uses a customer-managed KMS key, add the matching `kms:Decrypt` permission before deploy.

To keep KMS decrypt least-privilege, set `runtime_secret_kms_key_arns` only for the exact customer-managed keys that protect those secrets.

## DNS

This module intentionally **outputs** DNS data but does not manage DNS records. After the App Runner domain association is created, fetch the records:

```bash
terraform -chdir=infra output custom_domain_cname_record
terraform -chdir=infra output custom_domain_certificate_validation_records
```

Create:

1. a `CNAME` from `docs.authifi.io` to the reported `dns_target`
2. each certificate validation `CNAME` returned in `custom_domain_certificate_validation_records`

App Runner completes certificate validation after those records propagate.

## State Caveat

Terraform state will contain infrastructure metadata such as ARNs, service URLs, and the selected image identifier. It will **not** contain the secret values because only secret ARNs are supplied to App Runner.

## Rollback

Image rollback is an App Runner operation. Because Terraform ignores
`image_identifier`, `terraform apply` cannot perform or undo it.

Roll back to a previously pushed immutable SHA tag:

```bash
SERVICE_ARN="$(terraform -chdir=infra output -raw apprunner_service_arn)"
TARGET_IMAGE="$(terraform -chdir=infra output -raw ecr_repository_url):<previous-good-sha>"

aws apprunner describe-service --service-arn "$SERVICE_ARN" --output json > service.json

jq --arg image "$TARGET_IMAGE" '
  .Service.SourceConfiguration
  | .AutoDeploymentsEnabled = false
  | .ImageRepository.ImageIdentifier = $image
' service.json > source-configuration.json

aws apprunner update-service \
  --service-arn "$SERVICE_ARN" \
  --source-configuration file://source-configuration.json
```

Wait for the operation to finish and confirm the running image:

```bash
aws apprunner describe-service --service-arn "$SERVICE_ARN" \
  --query 'Service.{Status:Status,Image:SourceConfiguration.ImageRepository.ImageIdentifier}'
```

The service is rolled back once `Status` is `RUNNING` and `Image` matches
`$TARGET_IMAGE`. This is the same shape of call the deploy workflow makes, so
the next merge to `main` will roll forward normally from here.

For infrastructure rollback that is *not* about the image — IAM, autoscaling,
health checks, domain association — use ordinary Terraform:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars
```

Edge-layer rollback to the previous Cloudflare Pages hosting is documented in
`docs/operations/aws-oidc-hosting.md`.

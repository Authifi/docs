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
create_service      = false
image_identifier    = ""

oidc_issuer            = "https://issuer.example.com"
oidc_client_id         = "authifi-docs"
oidc_client_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:authifi/docs/oidc-client-secret-AbCdEf"
session_secret_arn     = "arn:aws:secretsmanager:us-east-1:123456789012:secret:authifi/docs/session-secret-ZyXwVu"

public_base_url    = "https://docs.authifi.io"
site_dir           = "/app/site"
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

Build from the repository root, tagging with an immutable commit SHA:

```bash
AWS_REGION=us-east-1
ECR_REPOSITORY_URL="$(terraform -chdir=infra output -raw ecr_repository_url)"
IMAGE_TAG="$(git rev-parse HEAD)"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$(printf '%s' "$ECR_REPOSITORY_URL" | cut -d/ -f1)"

docker build -t "$ECR_REPOSITORY_URL:$IMAGE_TAG" .
docker push "$ECR_REPOSITORY_URL:$IMAGE_TAG"
```

### Stage 3: create App Runner and domain association

```bash
IMAGE_IDENTIFIER="$(terraform -chdir=infra output -raw ecr_repository_url):$(git rev-parse HEAD)"

terraform -chdir=infra apply \
  -var-file=terraform.tfvars \
  -var='create_service=true' \
  -var="image_identifier=$IMAGE_IDENTIFIER"
```

## Apply After Bootstrap

After the service exists, keep `image_identifier` aligned with the desired immutable image tag in your tfvars or CLI arguments:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars
```

The GitHub deployment workflow later updates the running service to each new commit SHA without long-lived AWS keys.

## GitHub Actions OIDC

The deploy role trusts **only** the `Authifi/docs` `main` branch:

- audience: `sts.amazonaws.com`
- subject: `repo:Authifi/docs:ref:refs/heads/main`

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

## Runtime Configuration

Plain App Runner environment variables:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `PUBLIC_BASE_URL`
- `SITE_DIR`

Secrets injected from Secrets Manager by ARN:

- `OIDC_CLIENT_SECRET`
- `SESSION_SECRET`

The App Runner instance role is scoped to `secretsmanager:GetSecretValue` on only those two ARNs. If either secret uses a customer-managed KMS key, add the matching `kms:Decrypt` permission before deploy.

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

For infrastructure rollback:

```bash
terraform -chdir=infra apply \
  -var-file=terraform.tfvars \
  -var="image_identifier=$(terraform -chdir=infra output -raw ecr_repository_url):<previous-good-sha>"
```

For deployment rollback without changing Terraform, update App Runner back to a previously pushed immutable tag and wait for the operation to succeed:

```bash
aws apprunner update-service \
  --service-arn "<service-arn>" \
  --source-configuration file://source-configuration.json
```

Reuse the prior workflow's JSON-generation approach, swapping in the older SHA-tagged image.

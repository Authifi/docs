# AWS Infrastructure

This directory provisions the production hosting path for `Authifi/docs`:

- one internet-facing AWS Application Load Balancer in two supplied public subnets
- one private EC2 instance, with no public IP, in one supplied private application subnet
- a private S3 release bucket
- an SSM document plus IAM roles for GitHub Actions OIDC deployment and in-account host access
- an ACM certificate and the outputs needed to finish DNS validation outside Terraform

This root intentionally creates **no** VPC, subnets, route tables, NAT gateways, routes, or VPC endpoints. It reuses Authifi's existing shared VPC. The private application subnet must already route `0.0.0.0/0` through the shared NAT, because the server-side Authlib application performs OIDC discovery, JWKS fetches, and the authorization-code exchange itself, and the Ubuntu bootstrap installs packages on first boot.

The Terraform root intentionally does **not** configure a backend, so each caller keeps their own choice of state storage. Local state needs no setup; a remote backend needs one gitignored file before `init`. See [Init](#init).

## Files

- `versions.tf`: Terraform and provider version constraints
- `variables.tf`: caller-supplied AWS, network, and runtime inputs
- `main.tf`: ALB, EC2, IAM, S3, ACM, and SSM resources
- `outputs.tf`: deployment, DNS, and bootstrap outputs
- `terraform.tfvars.example`: starter variable file with placeholder infrastructure values only
- `scripts/deploy-release.sh`: on-host installer invoked through SSM

## Prerequisites

- Terraform `>= 1.6`
- AWS credentials with permission to create ALB, EC2, IAM, S3, SSM, and ACM resources
- An existing shared VPC and subnet layout:
  - `vpc_id`: the existing Authifi VPC
  - `public_subnet_ids`: exactly two distinct existing public subnets, in different AZs, for the ALB
  - `private_app_subnet_id`: an existing private application subnet for the EC2 instance
- A route from the private subnet to the shared NAT gateway
- Control of external DNS for `docs.authifi.io`, because ACM validation records are created outside Terraform
- An Authifi application registration for the docs site as a **public** client using Authorization Code + PKCE, with **no client secret**

## Variables

Start from the checked-in example:

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Fill in the shared-network and runtime values. The most important ones are:

- `aws_region`
- `vpc_id`
- `public_subnet_ids`
- `private_app_subnet_id`
- `oidc_issuer`
- `oidc_client_id`
- `public_base_url`
- `post_logout_path`
- `custom_domain_name`
- `existing_github_oidc_provider_arn` when the AWS account already has the shared GitHub OIDC provider

`public_base_url` must be the HTTPS origin users reach, and its host must equal `custom_domain_name`. `post_logout_path` must stay on the public allowlist; Terraform validates that at plan time so logout cannot be configured to bounce users straight back into login.

## Init

### Local state (the default)

The root declares no backend, so Terraform uses the implicit local backend and
no configuration is needed:

```bash
terraform -chdir=infra init
```

### Remote state supplied by the caller

`-backend-config` only supplies values for a `backend` block that already
exists. Passing it to a configuration with no such block does **not** fail —
Terraform prints a `Missing backend configuration` warning and initialises the
local backend anyway, so the state you meant to put in S3 lands on your disk.

Declare the backend first, in a file that is never committed. Terraform loads
`*_override.tf` last, so `infra/backend_override.tf` can introduce the block the
committed configuration deliberately omits:

```bash
cat > infra/backend_override.tf <<'EOF'
terraform {
  backend "s3" {}
}
EOF
```

Leave the block empty and pass your own settings as partial configuration:

```bash
terraform -chdir=infra init \
  -backend-config="bucket=my-tf-state" \
  -backend-config="key=authifi/docs/prod.tfstate" \
  -backend-config="region=us-east-1"
```

`infra/backend_override.tf` is gitignored. Keep it that way: it is the one place
a caller's account-specific state location would otherwise leak into the
repository. The same pattern works for any other backend — substitute
`backend "azurerm" {}`, `backend "gcs" {}`, or a Terraform Cloud `cloud` block.

Confirm the backend Terraform actually selected before applying. This matters
precisely because the failure is quiet: if the declaration is missing, `init`
still succeeds and writes state locally, so the check has to fail loudly rather
than print nothing.

```bash
check_terraform_backend() {
  local expected="${1:-s3}"
  local state_file="infra/.terraform/terraform.tfstate"

  if [ ! -f "$state_file" ]; then
    echo "No $state_file — terraform init has not run here." >&2
    return 1
  fi

  local actual
  actual="$(jq -r '.backend.type // empty' "$state_file")"
  if [ "$actual" != "$expected" ]; then
    echo "Terraform initialised the '${actual:-unknown}' backend, not '$expected'." >&2
    echo "State is not going where you think. Check infra/backend_override.tf." >&2
    return 1
  fi

  echo "Terraform is using the '$actual' backend."
}

check_terraform_backend s3
```

## Plan

```bash
terraform -chdir=infra plan -var-file=terraform.tfvars
```

## Bootstrap

There is an intentional two-stage ACM process. The first apply creates the ALB, EC2 instance, release bucket, IAM roles, SSM document, and ACM certificate request while leaving the public edge on HTTP only. The second apply turns on the HTTPS listener only after external DNS validation has made the certificate issuable.

### Stage 1: first apply with HTTPS disabled

`enable_https_listener` defaults to `false`, so the plain apply is the bootstrap apply:

```bash
terraform -chdir=infra init
terraform -chdir=infra plan -var-file=terraform.tfvars
terraform -chdir=infra apply -var-file=terraform.tfvars
```

Capture the outputs the workflow and DNS setup use:

```bash
terraform -chdir=infra output -raw aws_region
terraform -chdir=infra output -raw github_deploy_role_arn
terraform -chdir=infra output -raw release_bucket_name
terraform -chdir=infra output -raw instance_id
terraform -chdir=infra output -raw ssm_document_name
terraform -chdir=infra output -raw target_group_arn
terraform -chdir=infra output -raw alb_dns_name
terraform -chdir=infra output -raw alb_zone_id
terraform -chdir=infra output certificate_validation_records
```

Repository variables for `.github/workflows/deploy.yml` map to those values exactly:

- `AWS_REGION` from `aws_region`
- `AWS_DEPLOY_ROLE_ARN` from `github_deploy_role_arn`
- `RELEASE_BUCKET_NAME` from `release_bucket_name`
- `DOCS_INSTANCE_ID` from `instance_id`
- `DOCS_SSM_DOCUMENT_NAME` from `ssm_document_name`
- `DOCS_TARGET_GROUP_ARN` from `target_group_arn`
- `DOCS_ALB_DNS_NAME` from `alb_dns_name`
- `DOCS_PUBLIC_BASE_URL` from the same HTTPS origin you set in `public_base_url`

### Stage 2: publish DNS validation records, then enable HTTPS

Create the ACM validation records in your external DNS provider from `certificate_validation_records`, but do **not** move `docs.authifi.io` yet:

- if your provider supports ALIAS or ANAME records, keep `alb_dns_name` plus `alb_zone_id` ready for the later cutover
- in Cloudflare, publish only the certificate-validation records at this stage

Wait for ACM to report the certificate as `ISSUED`, then run the second apply:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars \
  -var='enable_https_listener=true'
```

The listener shape is deliberate:

- port 80 always exists
- before the certificate is ready, it serves a holding response
- after the certificate is ready, it redirects to 443
- port 443 exists only when `enable_https_listener=true`

That keeps the first apply unblocked on external DNS and makes the cutover a second, explicit step instead of a half-working one.

## GitHub Actions OIDC

The deploy role trusts **only** the deployment job in `Authifi/docs`, on `main`, in the `production` environment. Every claim is bound with `StringEquals`:

| Claim | Value | Terraform input |
|---|---|---|
| `aud` | `sts.amazonaws.com` | fixed |
| `sub` | `repo:Authifi/docs:environment:production` | `github_repository`, `deploy_environment` |
| `environment` | `production` | `deploy_environment` |
| `ref` | `refs/heads/main` | `deploy_branch` |
| `repository_id` | `993416679` | `github_repository_id` |

The subject is **not** `repo:Authifi/docs:ref:refs/heads/main`. The deployment job declares `environment: production`, and GitHub's default subject names the environment rather than the ref whenever a job references one, so the ref-form subject is a value this workflow can never present. Nothing in `terraform plan`, `terraform validate`, or the repository's tests would catch the mismatch on its own — the symptom is `Not authorized to perform sts:AssumeRoleWithWebIdentity` on the first real deployment, after the release archive has already been uploaded. `server/tests/test_ec2_infra.py` derives the expected subject from the workflow's own `environment:` value so the two cannot drift.

The branch is bound separately, through the token's own `ref` claim, because an environment-scoped subject says nothing about which branch the run started from. `repository_id` pins the numeric repository ID, which GitHub never reuses, so a repository deleted and recreated at `Authifi/docs` — or renamed into that path — does not inherit the trust. Read it with `gh api repos/Authifi/docs --jq .id`.

One caveat worth knowing before changing org policy: this is the **legacy, mutable** subject format, which is what this repository's OIDC customisation currently returns (`use_default` true, `use_immutable_subject` false — check with `gh api repos/Authifi/docs/actions/oidc/customization/sub`). Enabling immutable subjects rewrites the claim to `repo:Authifi@37509689/docs@993416679:environment:production`, and `local.github_repository_subject` in `main.tf` has to change in the same commit.

If the AWS account already has a shared GitHub OIDC provider, set `existing_github_oidc_provider_arn`. Otherwise this module creates the account-level provider for `https://token.actions.githubusercontent.com`.

The deploy workflow does not build or push a container image. It either builds a release archive from the current `main` commit or reuses an existing one, uploads the archive and checksum to S3, sends the release SHA to SSM, waits for the command to finish, waits for the target to report healthy, and then probes one public and one protected URL on `DOCS_PUBLIC_BASE_URL`.

For the first rollout, prefer a protected `production` environment so the first
post-merge run waits for approval. The workflow becomes manually dispatchable
only once this file exists on `main`, so the safest sequence is:

1. bootstrap infra and finish the ACM two-stage apply
2. configure the repository variables, including `DOCS_ALB_DNS_NAME`
3. merge the workflow to `main`
4. approve the pending run, or cancel it and use `workflow_dispatch` on `main`

If you merge before the variables exist or before the environment is protected,
the first push-triggered run may fail fast in `Verify required repository
variables`. That is honest but avoidable.

## Runtime Configuration

The instance bootstrap writes root-owned environment files and systemd runs the service as the non-root `authifi-docs` user. Terraform passes only non-secret values into user data:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `PUBLIC_BASE_URL`
- `SITE_DIR`
- `POST_LOGOUT_PATH`

`post_logout_path` defaults to `/privacy-policy/` and is validated at plan time: it must be site-relative, free of backslashes and control characters, and one of the paths the server actually serves publicly.

There is intentionally **no** `OIDC_CLIENT_SECRET` anywhere in production. This Authlib application is server-side and still needs outbound NAT egress, but the Authifi registration is a public client that uses PKCE instead of a secret. The session key is generated on the host at first boot and is never written into Terraform state.

## DNS

This module intentionally **outputs** DNS data but does not manage DNS records. Use:

```bash
terraform -chdir=infra output -raw alb_dns_name
terraform -chdir=infra output -raw alb_zone_id
terraform -chdir=infra output certificate_validation_records
```

The ALB record and the ACM validation records are both created outside Terraform.

## State Caveat

Terraform state will contain infrastructure metadata such as subnet IDs, ARNs, bucket names, and the public base URL. It does **not** contain an OIDC client secret or the session secret, because production uses a public OIDC client and the session key is generated on the host.

## Deploying Releases

Routine infrastructure changes use:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars
```

Application deployments happen through `.github/workflows/deploy.yml`:

- `push` to `main`: build a release archive for `GITHUB_SHA`, upload it to S3 if missing, deploy it through SSM, wait for ALB target health, then probe public and protected routes
- `workflow_dispatch` with `release_sha`: require an existing 40-character lowercase SHA, verify both S3 objects already exist, then redeploy that exact release without rebuilding

Those probes connect directly to `DOCS_ALB_DNS_NAME` with `curl --connect-to`
while still using the canonical `DOCS_PUBLIC_BASE_URL` hostname for TLS SNI,
certificate validation, redirects, and `Origin` semantics. The first workflow
therefore validates the new ALB without probing the old Cloudflare origin.

The installer at `infra/scripts/deploy-release.sh` is intentionally atomic:

1. verify the staged archive and checksum
2. unpack into `/opt/authifi-docs/releases/<sha>`
3. create the release virtualenv and install from the bundled wheelhouse
4. health-check the candidate on `127.0.0.1:18080`
5. atomically swap `/opt/authifi-docs/current`
6. restart `authifi-docs` under systemd
7. health-check the active service on `127.0.0.1:8080`
8. if either the restart or the active health check fails, restore the previous release symlink and restart the service again

Both post-swap failures take the same path out. A `systemctl restart` that returns non-zero is the same outcome as a failed health check one step earlier, so it rolls back rather than leaving `current` pointing at a release that never started.

If there was no previous release, a failed first activation removes `current` and stops the service instead of claiming success.

## Diagnostics

When a workflow deployment fails, start with the exact stage that failed:

- `Verify existing release for rollback`: the requested archive or checksum is missing from `s3://<release-bucket>/releases/`
- `Wait for installer`: the workflow already prints SSM command status plus stdout and stderr from `GetCommandInvocation`
- `Wait for healthy ALB target`: inspect the target health reason for the instance
- `Verify public and protected routes`: the workflow prints the unexpected headers and redirect target it observed

For host-level debugging, use Systems Manager rather than SSH. Useful checks from a Session Manager shell or Run Command are:

```bash
systemctl status authifi-docs
journalctl -u authifi-docs -n 200 --no-pager
readlink /opt/authifi-docs/current
curl -sS http://127.0.0.1:8080/health
```

## Instance Replacement

Some Terraform changes replace the EC2 instance, notably ones that change user data or the selected AMI. Replacement matters operationally:

- the new host gets a new `instance_id`, so refresh `DOCS_INSTANCE_ID` from `terraform output -raw instance_id`
- the host-side session key is generated on first boot, so replacement signs out existing browser sessions
- `/opt/authifi-docs/releases` starts empty on the new host, so run the deploy workflow after replacement to stage a release on it

The installer no longer lives in user data, so editing `infra/scripts/deploy-release.sh` updates the SSM document rather than forcing an instance replacement.

## Rollback

The smallest application rollback is a workflow dispatch of a previously published release SHA:

1. open the `Deploy` workflow in GitHub Actions
2. choose `Run workflow`
3. set `release_sha` to a known-good 40-character commit SHA whose release archive already exists in S3
4. run the workflow

That path does not rebuild anything. It verifies the archive and checksum already exist, then redeploys them through SSM.

For infrastructure rollback that is not about the deployed release content, use ordinary Terraform:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars
```

Historical edge rollback to the previous Cloudflare Pages delivery path is documented in `docs/operations/aws-oidc-hosting.md`.

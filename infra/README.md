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

### Deletion protection and access logs

`enable_alb_deletion_protection` defaults to `true`. The load balancer owns the name users reach the docs through and serves the certificate that name is covered by, so deleting it is a DNS-visible outage that one misdirected `terraform destroy` or stray `-target` can cause — and it is not recoverable in place, because the replacement has a different DNS name and the external record has to be moved again. Ordinary applies are unaffected; teardown is one apply longer:

```bash
terraform -chdir=infra apply -var-file=terraform.tfvars \
  -var='enable_alb_deletion_protection=false'
terraform -chdir=infra destroy -var-file=terraform.tfvars
```

ALB access logs are deliberately **not** enabled. An access log of this load balancer is a per-request record of which protected documentation page each session read, which is a more sensitive artifact than the documentation itself: it would need its own bucket, a region-specific log-delivery policy, a retention decision, and an access model at least as tight as the docs. The questions access logs usually answer here — is the target healthy, how many 5xx — are already answered by target health and the load balancer's CloudWatch metrics, and the application's own request handling is logged to the journal on the host (`journalctl -u authifi-docs`). If a genuine requirement for request-level audit appears, add it as a deliberate change with a retention policy rather than as a default.

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

The instance bootstrap writes root-owned environment files and systemd runs the service as the non-root `authifi-docs` user.

The whole release tree is root-owned and read-only to that user. `/opt/authifi-docs` is `0750 root:authifi-docs` so the service can traverse to `current/`, `releases/` is `0755 root:root`, and `incoming/` is `0700 root:root` because only root ever reads staged archives. The unit names no `ReadWritePaths` and adds `ReadOnlyPaths=/opt/authifi-docs` on top of `ProtectSystem=strict`. Releases are installed by root through Systems Manager, so the service account has no reason to own the code it runs — and a service account that can write there could replace that code and have systemd load it on the next restart.

Terraform passes only non-secret values into user data:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `PUBLIC_BASE_URL`
- `SITE_DIR`
- `POST_LOGOUT_PATH`

They travel as one JSON document (`local.host_config`, rendered with `jsonencode`) and land in `/etc/authifi-docs/config.json` at `0600 root`. Neither of the two things that read it evaluates anything:

- `deploy-release.sh` parses the JSON and hands the values to the candidate server through `env`, one word at a time. It requires exactly those five keys and refuses anything else, including a `PATH` or `LD_PRELOAD` that would otherwise decide what root executes and what it loads before `setpriv` drops privileges. The binaries it execs are absolute paths for the same reason.
- The bootstrap renders `/etc/authifi-docs/environment` from the same JSON, double-quoting and backslash-escaping each value, for systemd's `EnvironmentFile=`. `session.env` is generated on the host in the same format, holds only `SESSION_SECRET`, and is parsed rather than sourced too.

This replaced interpolating the five values into a file that the installer loaded with `source` as root. That made each of them root shell on the deployment path: an accepted absolute `site_dir` containing a space split into an assignment plus a command and aborted every deployment, and a command substitution or a semicolon in any value ran. Refusing punctuation was not an option — these are URLs and filesystem paths — and a shell metacharacter blacklist is never complete, so the format changed instead.

Control characters are the one thing refused rather than encoded, at plan time and again on the host. An `EnvironmentFile` assignment cannot represent a newline, so a value carrying one would silently become a shorter value plus a second assignment.

`post_logout_path` defaults to `/privacy-policy/` and is validated at plan time: it must be site-relative, free of backslashes and control characters, and one of the paths the server actually serves publicly.

There is intentionally **no** `OIDC_CLIENT_SECRET` anywhere in production. This Authlib application is server-side and still needs outbound NAT egress, but the Authifi registration is a public client that uses PKCE instead of a secret. The session key is generated on the host at first boot and is never written into Terraform state.

### Migrating an existing instance to `config.json`

Configuration used to be interpolated into `/etc/authifi-docs/environment` directly. Applying the change to `config.json` **replaces the instance**, because the file is written by user data and `user_data_replace_on_change = true` makes user data part of the instance's identity. There is no in-place upgrade path, and none is wanted: an instance still running the old bootstrap has the old `environment` file, which the current installer does not read.

Plan for a short outage and run it deliberately:

1. `terraform -chdir=infra plan` and confirm the plan replaces `aws_instance.docs` and nothing else unexpected. Note the current instance ID from `terraform -chdir=infra output -raw instance_id`.
2. Apply. The old instance is terminated and a new one boots with an empty `/opt/authifi-docs/releases`, so **the site is down from this point until a release is installed**. `current` does not exist, the service is enabled but not started, and the target group reports the target unhealthy.
3. Re-read the instance ID: `terraform -chdir=infra output -raw instance_id`. It has changed. Update the `DOCS_INSTANCE_ID` repository variable before deploying, or the deploy workflow will send its SSM command to an instance that no longer exists.
4. Redeploy. Run the deploy workflow with `workflow_dispatch` against the SHA that is supposed to be live, or push to `main`. This is what installs a release onto the new host and brings the site back.

Two things to avoid:

- **Do not start a deploy while the replacement is in progress.** The workflow reads `DOCS_INSTANCE_ID` as it runs. Started before the apply finishes, it installs a release onto the instance Terraform is about to terminate — the deployment reports success and the site stays down — or it fails against a terminated instance ID part-way through. Let the apply finish, update the variable, then deploy.
- **Expect every session to be logged out.** `SESSION_SECRET` is generated on the host at first boot and deliberately never stored in Terraform state, so the new instance generates a different one. Every cookie signed by the old instance is invalidated and readers log in again. This is the intended trade for keeping the secret out of state, and it applies to any instance replacement, not just this one.

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

- `push` to `main`: build a release archive for `GITHUB_SHA`, publish it to S3 if it is not already published, deploy it through SSM, wait for ALB target health, then probe the public route, the protected route, and the OIDC authorization redirect
- `workflow_dispatch` with `release_sha`: require an existing 40-character lowercase SHA, verify both S3 objects already exist, then redeploy that exact release without rebuilding

Publication answers all four states the two release objects can be in, and completes a partial one rather than refusing it. A run cancelled between the archive upload and its checksum upload used to leave a commit that could never be deployed again without a hand-repaired bucket; now the next run downloads whichever object is already there, compares it to the build in hand, and uploads only the missing one — refusing outright if the published bytes are not this build. Uploads go through `put-object --if-none-match "*"`, so a published release can never be overwritten even if something outside this workflow writes the same key.

The third probe is the one that exercises production OIDC. It asks the deployment for `/_auth/login` with no credentials and reads the `Location` it answers with, requiring an absolute HTTPS authorization endpoint on a host other than the docs host, carrying `client_id`, this deployment's `redirect_uri`, `response_type=code`, `state`, `nonce`, and an S256 PKCE challenge. Nothing else in the workflow contacts the OIDC client at all: the protected-route probe is served entirely locally, so with an unreachable issuer or a wrong discovery URL it answers its expected `307` and the deployment looked ready while every reader failed at the next sign-in. The probe's diagnostics name the parameters rather than echoing them, because that redirect carries a live `state` and `nonce` and workflow logs are not the place for them.

Of those steps, the route probes are the authoritative one. `Wait for healthy ALB target` uses `aws elbv2 wait target-in-service`, which returns as soon as every target in the group reports `healthy` — and because the health check interval is longer than the swap takes, a target that was healthy before the swap can still be reporting healthy just after it. The wait therefore establishes that the load balancer can reach the instance and that `/health` answers through the target group; it does not prove which release answered. Treat a green wait with a failed probe as a failed deployment, and read the probe output rather than the wait.

Those probes connect directly to `DOCS_ALB_DNS_NAME` with `curl --connect-to`
while still using the canonical `DOCS_PUBLIC_BASE_URL` hostname for TLS SNI,
certificate validation, redirects, and `Origin` semantics. The first workflow
therefore validates the new ALB without probing the old Cloudflare origin.

The installer at `infra/scripts/deploy-release.sh` is intentionally atomic:

1. verify the staged archive and checksum
2. unpack into `/opt/authifi-docs/releases/<sha>`
3. create the release virtualenv and install from the bundled wheelhouse
4. refuse to continue if `127.0.0.1:18080` is already taken, then health-check the candidate there, running it as `authifi-docs` through `setpriv`
5. atomically swap `/opt/authifi-docs/current`
6. restart `authifi-docs` under systemd
7. health-check the active service on `127.0.0.1:8080`
8. if either the restart or the active health check fails, restore the previous release symlink and restart the service again

Both post-swap failures take the same path out. A `systemctl restart` that returns non-zero is the same outcome as a failed health check one step earlier, so it rolls back rather than leaving `current` pointing at a release that never started.

If there was no previous release, a failed first activation removes `current` and stops the service instead of claiming success.

Two details of step 4 are worth knowing when reading installer output. The candidate is probed as the service account rather than as root, because the question the probe answers is whether the release systemd is about to start will actually serve — and root can read a site the service user cannot. And the port is bind-tested first: a leftover uvicorn from an interrupted deploy still holding 18080 would answer the health check, promoting a candidate that was never probed.

`incoming/<sha>` is cleared on every exit, including a rejected checksum, an unhealthy candidate, and a SHA that was already active. Nothing there is worth keeping — the same bytes are in S3 under the same SHA — and `aws:downloadContent` re-stages ahead of the installer on every invocation, so a retry never depends on what the last attempt left on disk. Only that SHA's directory is removed, never another deployment's.

`releases/<sha>` is discarded on the same exit whenever the run failed and that candidate did not become the live release. The directory is created before anything is extracted into it, so failed extraction, virtualenv creation, dependency installation, the candidate-port check, and the candidate health check all used to leave a full release tree behind — and `prune_releases` keeps the two most recently modified non-active release directories, so three failed attempts on one commit were three directories newer than the release a rollback needed, and the next successful deployment pruned the good one and kept the wreckage. A rolled-back activation is discarded too, because `abandon_activation` restores the previous release first: the single condition is whether `current` points at the candidate, which is also why an already-active SHA and a successful deployment both keep their trees.

Pruning old releases, the last thing the installer does, is the one step that is best effort. Reaching it means the release is already swapped in, restarted, and answering its health check, so a failure to delete a stale directory is housekeeping rather than a failed deployment. The installer prints `release pruning failed; deployment is active` on stderr and exits successfully. Treat that line as a real problem to fix — a host that stops pruning will fill its root volume eventually — but not as a reason to roll back. Every other failure past the swap still rolls back and exits non-zero.

## Diagnostics

When a workflow deployment fails, start with the exact stage that failed:

- `Verify existing release for rollback`: the requested archive or checksum is missing from `s3://<release-bucket>/releases/`
- `Wait for installer`: the workflow already prints SSM command status plus stdout and stderr from `GetCommandInvocation`
- `Wait for healthy ALB target`: inspect the target health reason for the instance. This step is necessary but not sufficient; it does not prove which release answered
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

Historical edge rollback to the previous Cloudflare Pages delivery path is documented in `operations/aws-oidc-hosting.md`.

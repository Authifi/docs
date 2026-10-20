# EC2 OIDC Hosting Design

## Goal

Host the MkDocs site in Authifi's AWS account behind Authifi OIDC, using the
smallest production architecture that keeps the application server off the
public internet.

The deployment must be easy to run at low volume, avoid container
infrastructure, and retain the public-path exceptions and hardened OIDC server
already implemented on `LSA-10037/aws-oidc`.

## Decisions

- Run one small EC2 instance in a private subnet.
- Terminate public HTTPS at an Application Load Balancer (ALB).
- Run the Python application directly under `systemd` in a virtual environment.
- Build MkDocs and package releases in GitHub Actions.
- Store immutable release archives in S3.
- Deploy to EC2 through AWS Systems Manager (SSM).
- Use GitHub Actions OIDC to assume the AWS deployment role.
- Do not use Docker, Docker Compose, GHCR, ECR, App Runner, SSH, or a
  self-hosted GitHub Actions runner in production.
- Keep Docker Compose only if it remains useful for the existing local mock
  OIDC test environment; local containers are not part of production.

## Architecture

The public ALB spans two existing public subnets in Authifi's shared VPC and
owns the ACM certificate. Port 80 redirects to port 443. The HTTPS listener
forwards to the application target on the EC2 instance in an existing private
application subnet. Security groups allow application traffic to EC2 only from
the ALB security group; EC2 has no public IP and accepts no inbound SSH.

The EC2 instance uses an encrypted EBS root volume. The application has no
persistent business data, so a separate data volume is unnecessary. Release
history lives in S3. A generated session-signing key remains on the instance;
replacement of the instance invalidates existing browser sessions, which is an
acceptable failure mode for this documentation service.

The instance runs the existing Starlette application with Uvicorn under
`systemd`. The service binds to the target-group port and reads the built site
from the active release directory. The ALB health check calls `/health`.

## Private-Network Access

Terraform accepts the shared VPC ID, two existing public subnet IDs, and one
existing private application subnet ID instead of creating another network.
The private subnet's existing default route uses Authifi's shared NAT Gateway.
That controlled egress is required for server-side OIDC discovery, signing-key
retrieval, authorization-code exchange, SSM, operating-system bootstrap, and
patching.

The app security group permits outbound DNS plus HTTP and HTTPS while allowing
inbound application traffic only from the ALB security group. The instance
retains no public address or inbound administrative port.

The selected AWS-provided Ubuntu 24.04 AMI contains Python 3.12 and the SSM
agent. Bootstrap installs the distribution's `python3-venv` package through the
shared NAT. An SSM Command document downloads the release and checksum from S3
using the instance role, so the host does not need the AWS CLI. Runtime Python
wheels remain bundled into each release archive, and release installation uses
no package index.

## Release Artifact

CI builds the MkDocs site and prepares one archive named by the Git commit SHA,
plus a SHA-256 checksum file. The archive contains:

- the built `site/` tree;
- the Python server package;
- the exact runtime requirements;
- a Linux x86-64 wheelhouse for those requirements;
- the instance-side deployment script or a versioned copy of it.

CI tests the application before publishing the archive. It also installs the
runtime dependencies from the wheelhouse with index access disabled, proving
the artifact is self-contained.

The S3 bucket blocks public access, encrypts objects, versions objects, and
expires old release artifacts after a configurable retention period. Object
keys are immutable commit SHAs. A workflow rerun reuses an existing release
only after its checksum matches; it never silently replaces different content
under the same SHA.

## Deployment Flow

The deployment workflow runs only for the protected production branch or an
explicit protected environment:

1. Build and verify the release archive.
2. Assume the AWS deployment role using GitHub OIDC.
3. Upload the SHA-addressed archive to S3.
4. invoke the deployment SSM Command document, which downloads the release and
   invokes the instance deployment script.
5. Wait for the command and ALB target health to succeed.
6. Probe the public compliance page and the protected-page redirect through the
   canonical HTTPS hostname.

The deployment role can upload only to the release prefix, send commands only
to the tagged docs instance, and inspect the target group. It cannot log into
the instance or manage unrelated infrastructure.

On the instance, deployment is serialized with a file lock. The script:

1. downloads the requested SHA-addressed artifact and verifies its checksum;
2. expands it into a new `/opt/authifi-docs/releases/<sha>` directory;
3. creates a release-local virtual environment;
4. installs only from the bundled wheelhouse;
5. runs a local health check against the candidate release;
6. atomically switches `/opt/authifi-docs/current`;
7. restarts the `systemd` service and verifies `/health`.

If the new service fails, the script restores the prior symlink and restarts
the prior release. Old local releases are pruned after a successful deployment,
while S3 retains the configured rollback history.

Rollback invokes the same SSM deployment command with an earlier SHA. There is
no separate rollback mechanism.

## Runtime Configuration and Secrets

Non-secret values are provisioned in a root-owned environment file:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `PUBLIC_BASE_URL`
- `SITE_DIR`
- `POST_LOGOUT_PATH`

The Authifi OIDC registration is a public client using authorization code flow
with PKCE S256 and `token_endpoint_auth_method=none`. The application does not
require an OIDC client secret in this mode. If Authifi tenant policy later
requires confidential-client authentication, that is a deliberate design
change and the secret must be placed in AWS Secrets Manager or SSM Parameter
Store, never committed to the repository.

The instance bootstrap generates a cryptographically random Starlette session
secret when absent, stores it in a root-owned file with mode `0600`, and reuses
it across service restarts. It is not stored in Git, Terraform state, GitHub
Actions, S3, or user data.

## Infrastructure as Code

Terraform replaces the current ECR and App Runner resources with:

- validated references to the shared VPC, two public subnets, and one private
  application subnet;
- ALB, listeners, target group, security groups, and ACM association;
- one EC2 instance with encrypted EBS and an instance profile;
- private release bucket and lifecycle policy;
- GitHub OIDC deployment role and least-privilege policies;
- outputs needed for DNS validation, repository variables, deployment, and
  verification.

DNS remains externally managed. Terraform outputs the ALB DNS target and ACM
validation records rather than changing the authoritative zone.

Terraform user data performs only stable host bootstrap: creates the service
account and directories, writes the `systemd` unit and deploy script, generates
the session key, and enables the service. Application releases remain owned by
the deployment workflow.

## Failure Handling

- ALB serves only healthy targets and reports the single target unhealthy if
  `/health` cannot read required site artifacts.
- Failed candidate installation never changes the active release.
- Failed post-switch health restores the previous release.
- SSM command output provides the deployment diagnostic trail.
- Concurrent deployments fail or wait on the deployment lock rather than
  interleaving files.
- Missing infrastructure configuration causes Terraform validation failure.
- Missing or malformed runtime configuration prevents the service from
  starting.
- OIDC protocol and callback failures continue to fail closed using the
  existing application behavior.

## Testing

The existing server, security, path-boundary, OIDC, and real-server tests
remain required.

New tests verify:

- Terraform contains no App Runner or ECR resources;
- EC2 has no public IP and receives application traffic only from the ALB;
- shared-network inputs, bucket, IAM, EBS encryption, egress, and ALB
  health-check configuration;
- user data creates the expected non-root `systemd` service and protected
  session key;
- the production workflow uses GitHub OIDC, S3, and SSM without Docker or
  registry login;
- release creation and offline wheel installation;
- successful deployment, failed-candidate preservation, post-switch rollback,
  locking, and explicit rollback to an older SHA;
- ALB-facing smoke probes preserve the intended public/protected boundary.

The existing Docker-based local mock OIDC smoke test remains optional developer
tooling. A direct local mode remains available with a Python virtual
environment and `mkdocs build`; production Docker is not required to develop
or test the application.

## Migration and Cutover

The existing App Runner/ECR implementation has not been applied as the target
production architecture and will be replaced on this branch rather than
maintained in parallel.

After Terraform creates the new stack and the first release is healthy:

1. validate the ALB through its AWS hostname where host checks permit;
2. publish ACM validation records;
3. point `docs.authifi.io` to the ALB DNS name;
4. run the public, protected, logout, traversal, header, and health probes;
5. disable the former Cloudflare Pages publication path.

Rollback during cutover points DNS back to the prior host. Application rollback
after cutover deploys an earlier S3 release SHA.

## Acceptance Criteria

- `docs.authifi.io` serves through an HTTPS ALB to one private EC2 instance.
- The EC2 instance has no public address or inbound administrative port.
- The OIDC and static-site application runs as an unprivileged `systemd`
  service.
- Protected and public paths behave exactly as covered by the existing tests.
- A public Authifi OIDC client completes code flow with PKCE and no client
  secret.
- GitHub-hosted Actions deploy through AWS OIDC, S3, and SSM.
- No production step uses Docker, a container registry, SSH, or a self-hosted
  runner.
- Deployments are atomic, health-checked, and rollback-capable.
- Local development and credential-free mock OIDC testing remain documented.

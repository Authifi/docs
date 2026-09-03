# Authifi Documentation

Product documentation for the Authifi identity and access management platform.

**Live site**: [https://docs.authifi.io](https://docs.authifi.io)

## Current Architecture

This repository builds a static MkDocs site, packages it into a container, and serves it behind a Starlette application that enforces Authifi OIDC login for protected docs.

- `docs/` contains the documentation source plus public discovery/legal assets.
- `mkdocs build --strict` produces the static site in `site/`.
- `server/` serves the built site, leaves specific legal/discovery paths public, and redirects protected paths through Authifi OIDC.
- `Dockerfile` builds the site and runtime image used locally and in AWS.
- `infra/` provisions ECR, IAM, and AWS App Runner for production hosting.
- `.github/workflows/ci.yml` validates PRs; `.github/workflows/deploy.yml` builds and deploys the image from `main`.

There are **no per-PR hosted preview environments in v1**. Pull requests get CI only. There is also **no Cloudflare Markdown-for-Agents conversion** in this architecture; agents can read the explicitly published public files and any protected HTML they can reach after an interactive browser login.

## Prerequisites

- Python 3.12 and a virtual environment for MkDocs and tests
- Docker with Compose support
- An Authifi tenant if you want to exercise the real OIDC flow locally
- AWS credentials and Terraform only when bootstrapping or operating production

Set up the local Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r server/requirements.txt pytest
```

## Local Development

### Fast content preview

Use MkDocs directly when you only need to review layout, copy, or navigation:

```bash
make serve
```

This serves the static site at `http://127.0.0.1:8000` without OIDC enforcement.

### Production-like OIDC locally

To run the containerized docs server against a real Authifi tenant, copy `.env.example` to `.env`, fill in the OIDC and session values, then start the stack:

```bash
cp .env.example .env
make local-up
```

The real-flow compose overlay requires:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`
- `SESSION_SECRET`
- `PUBLIC_BASE_URL` matching the URL you will open in the browser

### Credential-free local mock

Use the mock OIDC provider when you want the gated flow without depending on a live tenant:

```bash
make local-mock-up
```

This starts the docs server plus a local OIDC mock. The companion smoke test exercises the full login/logout path automatically:

```bash
make local-smoke
```

Shut down either local stack with:

```bash
make local-down
```

For Docker networking details, including the `host-gateway` requirement used by the mock issuer hostname, see [`docs/operations/aws-oidc-hosting.md`](docs/operations/aws-oidc-hosting.md).

## Authifi OIDC Client Registration

Register the docs site in Authifi as a **confidential Web App**.

- Redirect URI for local real-OIDC work: `http://localhost:8000/_auth/callback`
- Redirect URI for production: `https://docs.authifi.io/_auth/callback`
- Logout or post-logout return URL for local work: `http://localhost:8000/`
- Logout or post-logout return URL for production: `https://docs.authifi.io/`
- Requested scopes: `openid profile email`

If you run the docs server on a different base URL or port, update `PUBLIC_BASE_URL` and register the matching callback and logout destinations in Authifi.

## AWS Bootstrap And Deploy

Use [`infra/README.md`](infra/README.md) for the full Terraform and App Runner bootstrap commands. The short version is:

1. Apply Terraform with `create_service=false` to create ECR, IAM, and optional GitHub OIDC resources.
2. Build and push the first immutable image to ECR.
3. Apply Terraform with `create_service=true` and the pushed image identifier to create App Runner and the optional custom domain association.
4. Configure the production repository variables used by `.github/workflows/deploy.yml`:
   - `AWS_REGION`
   - `AWS_DEPLOY_ROLE_ARN`
   - `APP_RUNNER_SERVICE_ARN`
   - `ECR_REPOSITORY_URL`
5. Merge to `main` to let the deploy workflow build, push, and update the App Runner service.

[`docs/operations/aws-oidc-hosting.md`](docs/operations/aws-oidc-hosting.md) captures the repo-specific operating notes that sit on top of the raw infrastructure instructions.

## DNS Cutover And Rollback

After App Runner reports the custom domain association details, create the `docs.authifi.io` CNAME and any certificate-validation CNAMEs described in [`infra/README.md`](infra/README.md).

For cutover:

- lower DNS TTLs before the change window if your provider allows it
- create the new records
- wait for certificate validation and App Runner domain status to settle
- verify the public and protected paths listed below before announcing completion

For rollback:

- repoint `docs.authifi.io` back to the previous target if the issue is domain- or edge-related
- or redeploy / reconfigure App Runner to a previous known-good image tag if the issue is application-specific

## Post-Deploy Verification

Run these checks against production after a cutover or deploy:

```bash
curl -sI https://docs.authifi.io/
curl -sI https://docs.authifi.io/privacy-policy/
curl -sI https://docs.authifi.io/terms-of-service/
curl -sI https://docs.authifi.io/sms-opt-in.html
curl -sI https://docs.authifi.io/robots.txt
curl -sI https://docs.authifi.io/auth.md
curl -s https://docs.authifi.io/sitemap.xml
curl -s https://docs.authifi.io/.well-known/agent-skills/index.json
curl -sI https://docs.authifi.io/.well-known/api-catalog
```

Expected behavior:

- `/` returns an OIDC login redirect for unauthenticated requests
- `/privacy-policy/`, `/terms-of-service/`, `/sms-opt-in.html`, `/robots.txt`, `/auth.md`, `/sitemap.xml`, and `/.well-known/*` stay public
- `sitemap.xml` includes only the public legal URLs
- protected guides and security pages are absent from the sitemap

## Agent-Facing Assets

The build hook at `docs/hooks/agent_assets.py` publishes:

- `/robots.txt`
- `/auth.md`
- `/.well-known/api-catalog`
- `/.well-known/agent-skills/index.json`
- `/sitemap.xml`

`docs/_headers` remains source data for the server's root `Link` headers. It is not a hosting-platform contract in this deployment model.

## Versioning

This repository still uses [Changesets](https://github.com/changesets/changesets) for release-note management. Add a changeset only when the change should appear in the changelog:

```bash
npm run changeset:add
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contributor workflow, local preview options, CI expectations, and reviewer checklist.

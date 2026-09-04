# Authifi Documentation

Product documentation for the Authifi identity and access management platform.

**Live site**: [https://docs.authifi.io](https://docs.authifi.io)

## Current Architecture

This repository builds a static MkDocs site and serves it behind a Starlette application that enforces Authifi OIDC login for protected docs.

- `docs/` contains the documentation source plus public discovery/legal assets.
- `mkdocs build --strict` produces the static site in `site/`.
- `server/` serves the built site, leaves specific legal/discovery paths public, and redirects protected paths through Authifi OIDC.
- `infra/` provisions the production AWS path: an Application Load Balancer in two supplied public subnets, one private EC2 instance in a supplied private application subnet, a release bucket, and the IAM/SSM wiring for GitHub Actions deployments.
- `.github/workflows/ci.yml` validates PRs; `.github/workflows/deploy.yml` uploads release archives to S3 and deploys them through SSM from `main`.
- `Dockerfile` and Compose remain for local development and mock testing. They are not the production release path.
- `overrides/` holds the MkDocs Material theme override that keeps protected navigation out of the public legal pages.

There are **no per-PR hosted preview environments in v1**. Pull requests get CI only. There is also **no Cloudflare Markdown-for-Agents conversion** in this architecture; agents can read the explicitly published public files and any protected HTML they can reach after an interactive browser login.

## Authorization Policy

Authorization in v1 is **authentication only**. Any identity the configured Authifi tenant accepts may read every protected page, and the server keeps only the subject plus the optional email and name in the session. There is deliberately **no group, role, or email-domain filtering in v1**: access is controlled by controlling who can sign in to the tenant and who is assigned the docs application.

Anonymous callers learn nothing about the protected tree, not even which pages exist. A request for a protected directory page without its trailing slash answers with the login redirect rather than the canonical `308`, and the `next` parameter echoes the path as requested, so an existing page and a missing one are byte-identical to anyone who is not signed in. Public pages still canonicalise without a login, so `/privacy-policy` reaches `/privacy-policy/` as usual.

## Prerequisites

- Python 3.12 and a virtual environment for MkDocs and tests
- Docker with Compose support
- An Authifi tenant if you want to exercise the real OIDC flow locally
- AWS credentials and Terraform only when bootstrapping or operating production

Set up the local Python environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r server/requirements-dev.txt
```

Every version comes from the checkout, including pip's own: upgrading it would
resolve whatever is newest on PyPI today, and the runtime image is built on a
digest-pinned base precisely so it does not depend on the day.

Dependencies are locked twice over, once for each stage of the image, and each
lock is a pair of files:

| Direct dependencies, edit these | Complete closure, generated |
|---|---|
| `requirements.in` — the MkDocs site build | `requirements.txt` |
| `server/requirements.in` — the server runtime | `server/requirements.txt` |

Each `.txt` is the full transitive closure of its `.in`, resolved by a clean
install in the same digest-pinned base image the corresponding build stage uses,
and is the only file the Dockerfile and CI install. Change a direct dependency,
then regenerate the closure with the command in the lock's header. The two locks
hold shared packages at equal versions, because CI installs both into one
environment.

The tests in `server/tests/test_requirements.py` build both closures in a clean
container and fail if a lock is not exactly what its direct file resolves to, so
a stale lock is a red test rather than a surprise at deploy time.

## Local Development

### Fast content preview

Use MkDocs directly when you only need to review layout, copy, or navigation:

```bash
make serve
```

This serves the static site at `http://127.0.0.1:8000` without OIDC enforcement.

### Native release archive

Build the deployable release archive locally with:

```bash
make release
```

### Production-like OIDC locally

To run the containerized docs server against a real Authifi tenant, copy `.env.example` to `.env`, fill in the local OIDC and session values, then start the stack:

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

The smoke runner brings the stack up, drives login and logout against it, and tears it down again. Both URLs it uses can be moved, which is what you want when 8000 or 9400 is already taken:

```bash
python -m server.local_smoke --public-base-url http://localhost:9001
python -m server.local_smoke --mock-issuer http://oidc-mock.127.0.0.1.nip.io:9500
```

These configure the stack, not just the client: `--public-base-url` sets both `PUBLIC_BASE_URL` and the published `DOCS_PORT`, and `--mock-issuer` sets the provider's network alias, its published port, and the `OIDC_ISSUER` the docs container dials. The client then reads its settings back out of that same environment, so the two halves cannot disagree — which matters because logout checks `Origin` against `PUBLIC_BASE_URL`, and a client dialling an origin the server was not told about would see every sign-out refused. Setting `DOCS_PORT`, `PUBLIC_BASE_URL`, `MOCK_OIDC_HOST`, or `MOCK_OIDC_PORT` in `.env` or the environment works the same way and is what CI uses.

Both URLs have to be something the local stacks could actually answer on: `http://`, an explicit port from 1024 to 65535, and a host that resolves only to loopback. Anything else fails before Docker is touched, with a message naming the option. That is also a guard rather than pedantry — the runner tears the stack down with `--volumes` and writes a test user into whatever issuer it is given, neither of which belongs anywhere but a throwaway local stack.

`--mock-issuer` additionally requires a DNS hostname rather than an address, because that host is also the provider's Compose network alias: inside the docs container `127.0.0.1` is the docs container, so an address would point discovery at the docs server instead of the provider. `--public-base-url` has no such restriction and accepts `http://127.0.0.1:9001` or `http://[::1]:9001`.

Shut down either local stack with:

```bash
make local-down
```

For Docker networking details, including how the mock issuer hostname resolves from both the docs container and the host, see [`docs/operations/aws-oidc-hosting.md`](docs/operations/aws-oidc-hosting.md).

## Authifi OIDC Client Registration

Register the production docs site in Authifi as a **public client** using Authorization Code + PKCE. There is no production client secret.

- Redirect URI for local real-OIDC work: `http://localhost:8000/_auth/callback`
- Redirect URI for production: `https://docs.authifi.io/_auth/callback`
- Post-logout redirect URI for local work: `http://localhost:8000/privacy-policy/`
- Post-logout redirect URI for production: `https://docs.authifi.io/privacy-policy/`
- Requested scopes: `openid profile email`

Production registration values:

- Client type: `public`
- Grant: `authorization code`
- PKCE: `required`, `S256`
- Token endpoint authentication method: `none`

The server still performs OIDC discovery and the code exchange itself, so production keeps the private-subnet NAT egress even though the client is public. Local real-OIDC Compose remains separate: it can still use a local client secret for the tenant registration you choose to test with.

A sign-in lasts **eight hours, measured from the moment it completed**. The callback stamps the session with that time, and every protected request checks it, so using the site all day does not extend the session: at the eight-hour mark the next protected request clears the cookie and sends the user back through the issuer. The cookie's own `max_age` is still eight hours as well, but it answers a different question — Starlette re-issues the cookie on every response, so that clock restarts with each page view and only expires a browser that was left alone. A session with no stamp, an unparseable one, or one dated in the future is not a session: sign-ins predate this rule, and a replayed cookie can claim anything.

Logout is RP-initiated: the server clears the local session and, when the tenant publishes an `end_session_endpoint`, redirects there with `client_id` and `post_logout_redirect_uri`. Tenants without an `end_session_endpoint` fall back to a plain local redirect to the same path.

`/_auth/logout` answers `POST` only, and every gated page ends the session with a real `<form>` rather than a link. A `GET` gets a `405` naming `POST`, clears nothing, and never contacts the issuer, so an `<img src=".../_auth/logout">` on some other site — or a prefetcher, or a link scanner — cannot sign a reader out. Each `POST` must also carry an `Origin` matching the origin of `PUBLIC_BASE_URL`; a foreign, malformed, or absent one is refused with `403` before anything is cleared and before any outbound call. There is no CSRF token because every page here is a static file, so there is nowhere to mint one per render; `SameSite=Lax` already keeps the session cookie off a cross-site `POST`, and the `Origin` check is what turns that into a refusal and what covers the same-site-different-port case `Lax` treats as its own. Because that check is what makes sign-out work at all, `PUBLIC_BASE_URL` is validated as an absolute `http`/`https` URL at startup rather than at the first logout.

The post-logout target is always the configured `POST_LOGOUT_PATH`. A `?next=` on the logout URL is ignored: the issuer only accepts the `post_logout_redirect_uri` registered with it, and letting a caller influence that value would break every logout while handing them a say in a URI the issuer is asked to trust. The local fallback uses the same path so both flows land in the same place. Logging out with no session skips discovery entirely and redirects locally, so an anonymous caller cannot drive outbound requests to the issuer.

`POST_LOGOUT_PATH` must be one of the **exact** public pages the server serves, and the server validates it at startup rather than at the first logout, so a bad value fails the process immediately in local Compose and in production alike. Set it in `.env` for the local stacks, or through the `post_logout_path` Terraform variable for production, which the EC2 bootstrap writes as the same environment variable and which rejects the same values at plan time.

If you run the docs server on a different base URL or port, update `PUBLIC_BASE_URL` and register the matching callback and post-logout destinations in Authifi.

Because sign-out compares the browser's `Origin` against `PUBLIC_BASE_URL` and only forgives host case, that variable must name the host exactly as a browser would send it: no trailing dot, punycode rather than Unicode for an internationalised domain, and the canonical domain users actually browse. During cutover and diagnostics, do not browse the ALB hostname directly unless `PUBLIC_BASE_URL` names it; a browser origin on the ALB hostname is a different origin and logout will be refused there. See [`docs/operations/aws-oidc-hosting.md`](docs/operations/aws-oidc-hosting.md) for the details.

## AWS Bootstrap And Deploy

Use [`infra/README.md`](infra/README.md) for the full Terraform and EC2/ALB bootstrap commands. The short version is:

1. Apply Terraform once with `enable_https_listener=false` to create the ALB, private EC2 instance, release bucket, SSM document, IAM roles, and ACM certificate request.
2. Publish the ACM validation records in external DNS and wait for the certificate to become `ISSUED`.
3. Apply Terraform again with `enable_https_listener=true` to enable the HTTPS listener.
4. Configure the production repository variables used by `.github/workflows/deploy.yml`:
   - `AWS_REGION`
   - `AWS_DEPLOY_ROLE_ARN`
   - `RELEASE_BUCKET_NAME`
   - `DOCS_INSTANCE_ID`
   - `DOCS_SSM_DOCUMENT_NAME`
   - `DOCS_TARGET_GROUP_ARN`
   - `DOCS_ALB_DNS_NAME`
   - `DOCS_PUBLIC_BASE_URL`
5. Prefer a protected `production` environment so the first post-merge run on `main` waits for approval. The workflow becomes manually dispatchable only once it exists on `main`, so the safest first rollout is: configure the variables first, merge, then approve the pending run or cancel it and use `workflow_dispatch` on `main`.
6. Use the deploy workflow to build a release archive, upload it to S3, install it through SSM, wait for ALB target health, and probe the new ALB directly while preserving the canonical `DOCS_PUBLIC_BASE_URL` hostname in TLS and HTTP.
7. Cut `docs.authifi.io` over from Cloudflare only after those direct-ALB probes pass, then rerun the canonical verification against `https://docs.authifi.io/`.

If you merge the workflow before setting the required production variables or before protecting the `production` environment, the first push-triggered run may fail fast in `Verify required repository variables`. That failure is operationally honest, but it is avoidable.

After the deploy succeeds, the workflow requests `/privacy-policy/` and a protected guide URL through `curl --connect-to`, so it connects directly to `DOCS_ALB_DNS_NAME` while still presenting the canonical `DOCS_PUBLIC_BASE_URL` hostname for TLS SNI, certificate validation, redirects, and `Origin` semantics. A release that starts but cannot serve therefore fails before DNS cutover.

Rerunning the deploy workflow for a SHA that is already in S3 reuses the existing release artifact and continues to the SSM install, so reruns are safe. For rollback, dispatch the workflow with an earlier 40-character `release_sha`; see [`infra/README.md`](infra/README.md) for the exact procedure and the installer's on-host rollback behavior.

[`docs/operations/aws-oidc-hosting.md`](docs/operations/aws-oidc-hosting.md) captures the repo-specific operating notes that sit on top of the raw infrastructure instructions.

## DNS Cutover And Rollback

Create the ACM validation records first and leave `docs.authifi.io` on Cloudflare Pages until the new ALB has passed deployment probes.

For cutover:

- lower DNS TTLs before the change window if your provider allows it
- create only the ACM validation records first, without moving `docs.authifi.io` yet
- wait for certificate validation and the second Terraform apply to settle
- configure `DOCS_ALB_DNS_NAME` and the rest of the production workflow variables
- run the deploy workflow and let it connect directly to the ALB before cutover
- cut DNS from Cloudflare to the ALB only after those direct probes pass
- rerun the public and protected verification targets against `https://docs.authifi.io/` before announcing completion
- **disconnect the Cloudflare Pages Git integration** for the docs project, or disable its production and preview deployments, so a later push to `main` cannot silently republish an ungated copy of the site
- remove `docs.authifi.io` as a custom domain from the Cloudflare Pages project so it cannot reclaim the hostname
- keep the Pages project itself until the rollback window closes

For rollback:

- edge rollback: restore the `docs.authifi.io` `CNAME` to `authifi.pages.dev`, re-add `docs.authifi.io` as a Cloudflare Pages custom domain, and re-enable the Pages Git integration if it was disconnected during cutover — this restores the fully public, ungated site
- application rollback: redeploy a previous known-good 40-character release SHA through the `Deploy` workflow using the procedure in [`infra/README.md`](infra/README.md)

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

Also confirm that a public prefix cannot be used to reach protected content:

```bash
curl -sI --path-as-is 'https://docs.authifi.io/assets/%2e%2e/index.html'
curl -sI --path-as-is 'https://docs.authifi.io/assets/%2e%2e/search/search_index.json'
```

Expected behavior:

- `/` returns an OIDC login redirect for unauthenticated requests
- `/privacy-policy/`, `/terms-of-service/`, `/sms-opt-in.html`, `/robots.txt`, `/auth.md`, `/sitemap.xml`, and `/.well-known/*` stay public
- HTML pages are served as `text/html; charset=utf-8`, not `application/octet-stream`
- encoded traversal probes return `404`
- protected responses carry `Cache-Control: private, no-store` and `Vary: Cookie`
- `sitemap.xml` includes only the public legal URLs
- protected guides and security pages are absent from the sitemap and from the public legal pages' navigation

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

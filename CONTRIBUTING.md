# Contributing to Authifi Documentation

This repository now ships a protected documentation site: most pages require Authifi OIDC login in production, while a small set of legal and agent-discovery assets remain public. Contributing is still straightforward, but local validation matters more than it did for the old static-only hosting model.

## Choose Your Workflow

For quick text-only edits, GitHub.com or GitHub.dev is fine. For anything that touches navigation, generated assets, access behavior, or deployment docs, use a local checkout so you can run the same checks as CI.

## Local Preview Options

### Static MkDocs preview

Use this when you only need to inspect page content, styling, and navigation:

```bash
make serve
```

That runs `mkdocs serve` at `http://127.0.0.1:8000` with hot reload.

### Production-like OIDC preview

Use the real OIDC flow when you need to validate auth redirects, protected routes, or copy that references production behavior:

```bash
cp .env.example .env
make local-up
```

Set these values in `.env` first:

- `OIDC_ISSUER`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`
- `SESSION_SECRET`
- `PUBLIC_BASE_URL`

The matching Authifi confidential Web App must allow the callback URL at `http://localhost:8000/_auth/callback` and the local logout return URL at `http://localhost:8000/`, unless you intentionally run on another base URL.

### Mock OIDC preview

Use the mock flow when you want the protected-site behavior without tenant credentials:

```bash
make local-mock-up
```

This launches the docs server and a local OIDC test provider. Run the end-to-end smoke test with:

```bash
make local-smoke
```

Bring the local stack down with:

```bash
make local-down
```

## Writing And Navigation

- Write documentation in Markdown unless a page intentionally needs raw HTML, such as `docs/sms-opt-in.html`.
- Use one `#` title per page and consistent heading levels below it.
- Prefer relative links for internal docs references.
- Use admonitions and code fences when they clarify instructions.

Navigation is primarily governed by `docs/.nav.yml` through `mkdocs-awesome-nav`, not by a hand-maintained `nav:` block in `mkdocs.yml`. When you add or rename navigable pages, update `docs/.nav.yml` in the relevant section.

## Public Versus Protected Content

Keep the access model in mind while editing:

- Public: legal pages, selected discovery files, static assets, and `sitemap.xml`
- Protected: the main product documentation, including guides, authorization content, security pages, and the home page

If you add a new public page or public machine-readable asset, update the server allowlist, `docs/auth.md`, `docs/robots.txt`, and any sitemap-generation logic together.

## Pull Request Checks

PRs do **not** get hosted preview deployments in v1. Instead, `.github/workflows/ci.yml` runs:

- server tests
- strict MkDocs build
- container build
- Terraform format and validate checks for `infra/`

Before opening or updating a PR, run the focused checks that match your change. For broad changes, run the full local set:

```bash
PYTHONPATH=. .venv/bin/pytest server/tests
make build
docker build --tag authifi-docs:test .
```

## Reviewer Checklist

Reviewers should confirm:

- the page content is accurate and internally consistent
- new or changed navigation is reflected in `docs/.nav.yml` when appropriate
- public vs. protected behavior still matches `docs/auth.md`, `docs/robots.txt`, and the server allowlist
- generated assets such as `sitemap.xml` do not leak protected docs
- local or CI verification covers the changed behavior
- deploy or operations docs still match the AWS/App Runner/OIDC implementation

## Need Help?

- Use [README.md](README.md) for architecture, local run modes, and deployment context
- Use [`infra/README.md`](infra/README.md) for Terraform and AWS bootstrap details
- Open a GitHub issue or ask the Authifi maintainers when product behavior is unclear

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

The matching Authifi confidential Web App must allow the callback URL at `http://localhost:8000/_auth/callback` and the post-logout redirect URI at `http://localhost:8000/privacy-policy/`, unless you intentionally run on another base URL.

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
- Authorization is authentication only: any identity the configured Authifi tenant accepts can read every protected page. There is no group, role, or domain filtering in v1.

If you add a new public page or public machine-readable asset, update these together:

- `PUBLIC_EXACT_PATHS` / `PUBLIC_PREFIXES` in `server/app.py`
- the public path list in `docs/auth.md`
- every `Allow:` block in `docs/robots.txt`
- `PUBLIC_SITEMAP_PATHS` and, for rendered Markdown pages, `PUBLIC_PAGE_SOURCES` in `docs/hooks/agent_assets.py`

`server/tests/test_public_boundary.py` compares those sources against each other and against the built site, so leaving one behind fails CI. A new public Markdown page must be listed in `PUBLIC_PAGE_SOURCES`, which marks it `hide: [navigation, search]`. `overrides/main.html` reads that metadata and removes two pieces of chrome that would otherwise reach into protected territory: the navigation, which would advertise every protected guide by title and URL, and the search control, which can only ever fail because the search index is protected.

Search on public pages is removed by swapping in `overrides/partials/header-public.html`, a verbatim copy of Material's header with the search block deleted. Protected pages keep Material's stock header, so search there is untouched. A `mkdocs-material` upgrade that changes the header fails `test_public_header_is_the_material_header_minus_search`, which re-derives the copy from the installed theme; regenerate the file when that happens rather than editing it by hand.

## Pull Request Checks

PRs do **not** get hosted preview deployments in v1. Instead, `.github/workflows/ci.yml` runs:

- server tests, including the built-artifact public-boundary tests
- strict MkDocs build
- container build
- a rootless, read-only container run that probes `/health`, HTML content types, and encoded-traversal bypasses
- the credential-free mock OIDC smoke, with teardown guaranteed
- Terraform format and validate checks for `infra/`

Before opening or updating a PR, run the focused checks that match your change. For broad changes, run the full local set:

```bash
.venv/bin/python -m pytest server/tests
make build
docker build --tag authifi-docs:test .
make local-smoke
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

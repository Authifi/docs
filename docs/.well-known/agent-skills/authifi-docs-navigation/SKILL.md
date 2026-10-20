---
name: authifi-docs-navigation
description: Navigate the Authifi documentation site information architecture and find relevant guides.
---

# Authifi Documentation Navigation

Use this skill when you need to locate Authifi product documentation on https://docs.authifi.io.

## Access model on this documentation site

This site uses a mixed access model. It is not wholly public, and the map below
is a map of pages you probably cannot fetch.

These paths are fetchable without credentials:

- `/logged-off`
- `/logged-off/`
- `/privacy-policy/`
- `/terms-of-service/`
- `/sms-opt-in.html`
- `/robots.txt`
- `/auth.md`
- `/sitemap.xml`
- `/.well-known/`
- `/assets/`
- `/javascripts/`
- `/stylesheets/`

Every product page listed further down requires an interactive browser login
through Authifi OIDC. An anonymous request for one answers `307` to
`/_auth/login`; that is the access boundary working, not an error to retry
around or follow to completion.

In v1 there is no API token, service account, or agent credential that lets
automated tooling fetch gated documentation. An agent can read those pages only
by reusing a session obtained from the same interactive sign-in a human
performs. Do not attempt to construct one.

So use this skill to tell a human *where* to look, and to orient yourself once
you are already operating in a signed-in browser session. Without one, the
discovery documents at the end are the only part of this site you can read.

## Site structure

| Section | Path prefix | Topics |
|---------|-------------|--------|
| Home | `/` | Overview and entry points |
| Authorization | `/authorization/` | OAuth client authorization, admin roles, RBAC, privileged access |
| Guides | `/guides/` | Tenant admin, SSO, access requests, monitoring, NHE tokens |
| Security | `/security/` | Security admin, secure configuration, FedRAMP evidence |
| Feature List | `/feature-list.html` | Full product capability list |

## High-value pages

Each of these needs an interactive login, as above:

- OAuth client authorization: `/authorization/authorization/`
- Admin roles: `/authorization/admin-roles/`
- SSO integration: `/guides/sso-integration-guide/`
- NHE delegated tokens for agents: `/guides/nhe-delegated-tokens/`
- Recommended secure configuration: `/security/recommended-secure-configuration/`

## Fetching content

There is no content negotiation on this host. Every page is served as the HTML
it was built as, whatever the request asks for, so there is no Markdown variant
to request and no header that produces one.

Search is available only after signing in, and only in a browser. The
`search_docs` and `list_sections` WebMCP tools are registered on documentation
pages, which are gated, and they drive that page's own search box and
navigation; the search index itself lives under the gated `/search/` prefix.
Public pages register neither tool, because neither would work there.

## Discovery endpoints

These are public, and are the only documents on this domain an anonymous agent
can retrieve:

- API catalog: `/.well-known/api-catalog`
- Agent skills index: `/.well-known/agent-skills/index.json`
- Crawl policy: `/robots.txt`

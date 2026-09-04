---
name: authifi-oauth-concepts
description: Understand Authifi OAuth 2.0 and OIDC concepts as documented, without calling live product APIs.
---

# Authifi OAuth and OIDC Concepts

Use this skill when explaining or planning integration with Authifi as an authorization server. This covers documented product behavior, not live API calls on the docs domain.

## Authifi as authorization server

Authifi acts as an OAuth 2.0 authorization server and OpenID Connect provider. Each tenant can define user groups, assign OAuth clients, and restrict access by group membership.

## Access model on this documentation site

This site uses a mixed access model. It is not wholly public.

These paths are fetchable without credentials:

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

Every other page, including all of the documentation linked below, requires an interactive browser login through Authifi OIDC. An anonymous request for one answers `307` to `/_auth/login`; that is the access boundary working, not an error to retry around.

In v1 there is no API token, service account, or agent credential that lets automated tooling fetch gated documentation. An agent can read those pages only by reusing a session obtained from the same interactive sign-in a human performs. Do not attempt to construct one.

## Key documentation

Each of these requires an interactive login, as above:

- OAuth client authorization: `/authorization/authorization/`
- SSO integration and client setup: `/guides/sso-integration-guide/`
- Admin roles and API scopes: `/authorization/admin-roles/`
- NHE delegated tokens for LLM agents: `/guides/nhe-delegated-tokens/`

## Discovery on product deployments

OAuth and OIDC discovery endpoints are published per tenant on Authifi product deployments, not on the documentation site. Typical paths include:

- `/.well-known/openid-configuration`
- Tenant-scoped authorize and token endpoints under `/_api/auth/<tenant>/`

Refer to the SSO Integration guide for issuer configuration, client registration, redirect URIs, PKCE, and supported grant types.

## Agent delegation

For short-lived tokens delegated to automated agents or LLM pipelines, read the NHE Delegated Tokens guide. It covers RFC 8693 token exchange, actor tokens, and tenant-level configuration.

## Important distinction

The documentation host at docs.authifi.io is an OIDC **client**. It is not an OAuth authorization server: there is no authorize endpoint, token endpoint, or userinfo endpoint on this domain, and no tenant OAuth metadata to discover here. The only machine-readable documents this domain publishes are the agent-skills index and the API catalog under `/.well-known/`.

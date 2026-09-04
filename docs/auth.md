# auth.md

Agent access instructions for the Authifi Documentation site.

## Audience

AI agents and automated tooling that read Authifi product documentation.

## Access model

This site uses a mixed access model.

The following paths are intentionally public:

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

All other documentation paths require an interactive browser login through Authifi OIDC. There is no API-token bypass for protected docs.

## Discovery

- API catalog: `/.well-known/api-catalog` (`application/linkset+json`)
- Agent skills index: `/.well-known/agent-skills/index.json`
- Crawl policy: `/robots.txt`

## Authifi product OAuth and OIDC

The documentation host is an OIDC **client**. It is not an OAuth authorization server, token endpoint, userinfo endpoint, or product API.

Authifi deployments expose OAuth 2.0 and OpenID Connect discovery per tenant on the product domain (for example `/.well-known/openid-configuration` under each tenant path). See the [SSO Integration guide](/guides/sso-integration-guide/) for product OAuth behavior, client configuration, and issuer management.

For agent delegation with short-lived tokens, see [NHE Delegated Tokens](/guides/nhe-delegated-tokens/).

## Content usage

This site declares the following content preferences in `/robots.txt`:

- `ai-train=no`
- `search=yes`
- `ai-input=yes`

Automated agents can fetch the public files listed above without credentials. They can read protected docs only if they can complete the same interactive browser login flow as a human user. This site does not issue agent-specific access tokens for documentation scraping.

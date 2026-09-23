---
title: How to start login
---

<!-- Generated from idbroker: packages/auth/docs/login-initiation.md. Edit it there, not in Authifi/docs. -->

# How to start login (OIDC vs SAML vs WS-Fed)

The endpoint that starts login depends on the **application type**. Starting an OIDC app at `/auth/login` skips the authorization request, so Authifi has no interaction to resume after the identity provider returns.

The hosted picker (`/auth/login`) is not rendered on every login. A matching `connection` can send OIDC and standard SAML flows directly to the selected identity provider. InCommon flows use the federation redirect. A client with one assigned identity provider can also bypass the picker. Existing SSO sessions can finish without rendering it. WS-Fed does not forward `connection` from `/wsfed`, so its normal login page rules apply.

All paths below are relative to the Auth API base path (usually `/_api` or `/_api/v2`).

## Choose the start URL by application type

| App type                           | Start here                          | What you get back                                                                                                                |
| ---------------------------------- | ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| OIDC redirect (web / SPA / native) | `/auth/{tenantId}/authorize`        | Authorization code, then tokens (**recommended**, with PKCE for public clients). Implicit `id_token` still exists for some SPAs. |
| SAML                               | `/auth/{tenantId}/{clientId}/saml`  | SAML assertion to the service provider                                                                                           |
| WS-Fed                             | `/auth/{tenantId}/{clientId}/wsfed` | WS-Fed token to the relying party                                                                                                |

Device Authorization Grant clients start at `POST /auth/{tenantId}/device/auth`, then the browser opens the returned `/auth/{tenantId}/device` verification URI. They do not start at `/authorize`.

`/auth/login` is the hosted picker (WAYF). Authifi redirects **to** it after an OIDC or SAML start URL when no valid `connection` is supplied. Do not configure it as a Palantir, Auth0-style, or other OIDC client's authorization endpoint. A legacy `/login` start for SAML is documented in the [appendix](#appendix-legacy-login-start-for-saml). It is not a working WS-Fed start.

## OIDC

Start a normal authorization-code request. That creates the short-lived OIDC interaction (`_interaction` cookie) Authifi needs after NIH Login, InCommon, or any other IdP returns.

```
GET /auth/{tenantId}/authorize
  ?client_id={clientId}
  &redirect_uri={registeredCallback}
  &response_type=code
  &scope=openid profile email
  &state={state}
```

Add PKCE for public clients. Confidential clients still authenticate at the token endpoint with their registered method (secret or assertion); they may also send PKCE. Clients registered only for an implicit or hybrid `response_type` (`id_token`, `id_token token`, `code id_token`, …) will not receive an authorization code from `response_type=code`.

Optional IdP hints on **this same request**:

- `connection` — identity provider name; OIDC also accepts a unique issuer URL alias
- `in_common_entity_id` — InCommon organization, used with `connection={inCommonProviderName}`

Authifi authenticates the user through the selected or configured identity provider, then resumes `/auth/{tenantId}/authorize/{interactionId}` to issue the code to `redirect_uri`.

The Admin UI **Test login** link (`/auth/test-client`) redirects public clients to `/authorize`. Confidential clients use a secret form or a stored secret. The test flow does not redeem `client_secret_jwt` or `private_key_jwt` clients because it cannot create the required JWT client assertion.

### PKCE and client authentication

Both public and confidential clients use the authorization-code grant. PKCE binds the code to the client that started `/authorize`. It does not replace confidential-client authentication at `POST /auth/{tenantId}/oidc/token`, also advertised as `token_endpoint` in discovery. If a confidential client sent PKCE, the token request must include `code_verifier` and the registered secret or assertion. Omitting the latter yields `invalid_client`.

| Client                                                      | How to start `/authorize`                                 | How to redeem the code                                                                                                                                         |
| ----------------------------------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public / native / SPA (`tokenEndpointAuthMethod` is `none`) | Include `code_challenge` and `code_challenge_method=S256` | Send `client_id` and `code_verifier`. No client secret.                                                                                                        |
| Confidential (`client_secret_post`)                         | PKCE is optional                                          | Send `client_id` and `client_secret` in the form body. Also send `code_verifier` if PKCE was used.                                                             |
| Confidential (`client_secret_basic`)                        | PKCE is optional                                          | Send `Authorization: Basic` with `base64(client_id:client_secret)`. Do not send a separate `client_id` form field. Also send `code_verifier` if PKCE was used. |
| Confidential (`client_secret_jwt` or `private_key_jwt`)     | PKCE is optional                                          | Send `client_assertion` and `client_assertion_type`. `private_key_jwt` has no client secret. Also send `code_verifier` if PKCE was used.                       |

Authifi does not currently require PKCE for any client. Public clients should still use it. Only `S256` is supported. Discovery advertises `token_endpoint_auth_methods_supported`; match the method registered on the client.

Every token POST also sends `grant_type=authorization_code`, the returned `code`, and the same `redirect_uri` used at `/authorize`. The rows above only list the extra client-authentication and PKCE fields.

### What goes wrong if you start at `/auth/login`

This helper URL is used after `/authorize` has created the interaction:

```
GET /auth/login
  ?tenant={tenantId}
  &client={clientId}
  &protocol=oidc
  &redirect_uri={callback}
  &connection={inCommonProviderName}
  &in_common_entity_id={entityId}
```

If an OIDC app starts here, Authifi has no interaction to resume:

- A missing `_interaction` cookie produces `redirect_uri?error=expired_interaction_session` when authentication starts.
- An expired interaction produces `/auth/expiredInteraction` after the identity provider callback.

`/authorize` creates the interaction. `/auth/login` does not.

## SAML

For a SAML application, Authifi is the IdP. Both IdP-initiated and SP-initiated login use:

```
GET /auth/{tenantId}/{clientId}/saml
```

A **bare GET** is IdP-initiated. Standard SAML clients then receive an unsolicited assertion. For **SP-initiated** login, the service provider sends a `SAMLRequest` and usually `RelayState` with Redirect or POST binding. Authifi preserves that request so the response has the correct destination and `InResponseTo`. An SP that requires response correlation may reject the unsolicited assertion from a bare GET.

Google Workspace and Jira custom integrations can run user provisioning during an IdP-initiated flow instead of returning the standard assertion response.

Optional:

- `connection` — select the upstream identity provider
- `entityId` — InCommon organization (requires `connection`)

SAML does **not** need an OIDC interaction. After the upstream identity provider returns, Authifi issues a SAML assertion to the service provider for standard SAML clients.

## WS-Fed

For a WS-Fed application, start at:

```
GET /auth/{tenantId}/{clientId}/wsfed
  ?wa=wsignin1.0
  &wtrealm={realm}
  &wreply={replyUrl}
```

That endpoint stores `wtrealm` / `wreply` and creates the WS-Fed session. Hosted login with `protocol=wsfed` does not create this session. WS-Fed does not use the OIDC `_interaction` cookie. After the upstream identity provider returns, Authifi issues a WS-Fed token to the relying party.

## InCommon

The NIH Federation / campus IdP chain is the same either way. Put the org hint on the **protocol start URL**:

| Protocol | Parameters                                                                    |
| -------- | ----------------------------------------------------------------------------- |
| OIDC     | `connection={inCommonProviderName}` and `in_common_entity_id` on `/authorize` |
| SAML     | `connection={inCommonProviderName}` and `entityId` on `/saml`                 |

`connection` is the identity provider **name**, which is often `InCommon` but can be a custom name. OIDC also accepts a unique issuer URL alias. Hosted login uses `in_common_entity_id`. The SAML SSO endpoint uses `entityId`.

## Related

- [OIDC request scopes](https://docs.authifi.io/guides/oidc-request-scopes/)
- [SSO integration guide](https://docs.authifi.io/guides/sso-integration-guide/)
- [Authifi documentation](https://docs.authifi.io/)

## Appendix: legacy `/login` start for SAML

This option is not recommended. We have included it here for completeness in case some legacy SAML applications are using it.

SAML does not need an OIDC interaction, so older SAML applications may start at hosted login instead of the SAML SSO endpoint. After upstream authentication, Authifi redirects to `redirect_uri`, which must be an **absolute** Authifi SAML URL.

```
GET /auth/login
  ?tenant={tenantId}
  &client={clientId}
  &protocol=saml
  &redirect_uri=https://{auth-host}{apiBase}/auth/{tenantId}/{clientId}/saml
  &connection={inCommonProviderName}
  &in_common_entity_id={entityId}
```

`{apiBase}` is `/_api` or `/_api/v2`. `{auth-host}` is the public Authifi host. `{inCommonProviderName}` is the IdentityProvider `name`.

Do not point `redirect_uri` at the service provider. The SP would receive a browser redirect with no `SAMLResponse`.

Do not use `protocol=oidc` or `protocol=wsfed` on `/auth/login` as the application entry point. OIDC fails as described above (`expired_interaction_session`). WS-Fed starts at `/wsfed` with `wa`, `wtrealm`, and `wreply`. New SAML and WS-Fed integrations should start at `/saml` and `/wsfed`.

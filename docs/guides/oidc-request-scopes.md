---
title: OIDC Request Scopes
---

<!-- Generated from idbroker: packages/auth/docs/oidc/request-scopes.md. Edit it there, not in Authifi/docs. -->

# Request Scopes

This document describes each scope listed in the OpenID Provider discovery document and shows how to request it in common OAuth2/OIDC flows.

## How scopes work in this server

Scopes are requested in the authorization request using the OAuth2/OIDC scope parameter. The server uses scopes to decide which claims can appear in the ID token and/or the UserInfo response. The presence of a scope does not guarantee that a claim will appear in the ID token. Some claims may only be available via the UserInfo endpoint depending on server configuration and token size limits.

**Rules of thumb:**

- Request only what you need. Over-scoping increases review friction and user consent fatigue.
- Most profile data is returned from the UserInfo endpoint; ID tokens are meant for identity, not bulk profile payloads.
- Use `offline_access` only when you truly need refresh tokens for long-lived sessions or background jobs.

Two kinds of scopes appear together on tokens:

- **Login scopes** — standard and custom OIDC scopes in this document (`openid`, `profile`, `email`, `custom_claims`, …). They mainly control which identity claims appear in the ID token and/or UserInfo.
- **Resource scopes** — API permissions registered on a resource server (for example `mock:api:read`). They authorize calls to that API and are typically filtered by the `resource` parameter ([RFC 8707](https://www.rfc-editor.org/rfc/rfc8707)).

### Narrowing resource scopes (`requireAuthorizationScopesInRequest`)

The setting that controls this behavior is the per-client feature toggle:

`client.config.featureToggles.requireAuthorizationScopesInRequest`

(In the Auth admin UI this is labeled **Require Resource Indicator scopes**.)

This is distinct from `requireResourceIndicatorInRequest`, which only requires the OAuth `resource` parameter (RFC 8707 audience) to be present — it does **not** narrow which scopes land on the token.

By default (`requireAuthorizationScopesInRequest` **off**), when a user authenticates the access token may receive **all** of that user's applicable RBAC resource scopes for the target API, even if the client only requested login scopes such as `openid`. The issuer expands the effective scope set (via `assignScopesToParams`) to include those RBAC scopes.

When `requireAuthorizationScopesInRequest` is **on**:

- For interactive authorization-code and device flows, include the needed resource scopes on the **authorize** / **device authorization** request (the grant is created there). Sending them only on the later token exchange is too late.
- For client-credentials, include them on the token request `scope` parameter (see Client Credentials below).
- The access token's signed `scope` claim carries only the **requested subset** of resource scopes that the user/client is allowed (not the full auto-granted set).

Resource scopes are resolved for a target API. For API-specific permissions, also send the OAuth `resource` parameter ([RFC 8707](https://www.rfc-editor.org/rfc/rfc8707)) with that API's identifier. Without `resource`, the issuer may mint a token for the default/no audience and not attach the API scopes shown in the examples below.

Login scopes such as `openid` still belong in the authorize request (and control UserInfo / grant identity claims), but they are **not** listed in a JWT access token's signed `scope` claim used by resource servers.

| `requireAuthorizationScopesInRequest` | Behavior                                                                                            | Signed access-token `scope` (resource scopes)    |
| ------------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **false** (default)                   | Issuer auto-grants all of the user's applicable RBAC scopes for the API, regardless of the request. | The user's full applicable RBAC set              |
| **true**                              | Client must list the resource scopes it wants in the request.                                       | Exactly the requested subset the user is allowed |

**Example** — user has RBAC scopes `mock:api:read mock:api:write` on API `X` (identifier `https://api.example/x`):

```http
GET /_api/auth/{tenantId}/authorize?
  client_id=YOUR_CLIENT_ID
  &redirect_uri=https%3A%2F%2Fyourapp.example%2Fcallback
  &response_type=code
  &scope=openid%20mock%3Aapi%3Aread
  &resource=https%3A%2F%2Fapi.example%2Fx
  &code_challenge=BASE64URL(SHA256(verifier))
  &code_challenge_method=S256
  &state=RANDOM
  &nonce=RANDOM
```

| `requireAuthorizationScopesInRequest` | Request `scope` (+ `resource` for API `X`) | Signed access-token `scope`    |
| ------------------------------------- | ------------------------------------------ | ------------------------------ |
| `true`                                | `openid mock:api:read`                     | `mock:api:read`                |
| `true`                                | `openid`                                   | _(none / omitted)_             |
| `false`                               | `openid`                                   | `mock:api:read mock:api:write` |

Set it to `true` when an app should mint least-privilege tokens (for example, only `read` even though the user also has `write`). Leave it `false` when clients should not have to enumerate every API permission on each request.

Configure it via the client API (`config.featureToggles.requireAuthorizationScopesInRequest`) or in the Auth admin UI under the client's feature toggles (**Require Resource Indicator scopes**).

### Which API a client may request

For a **custom** resource server, the client must be associated with that API (Manage APIs → clients on the resource server) before the issuer will mint a user or client-credentials access token whose `aud` matches that identifier. An unassigned `resource` value is rejected (`invalid_target`).

The tenant **default** management audience (`https://{issuer}/_api/auth/{tenantId}`) is the exception: if the request omits `resource`, or sends that default identifier, the issuer does **not** require a client–API association unless `requireResourceIndicatorInRequest` is on.

That association is **all-or-nothing per API**. It does not choose _which_ resource scopes a **user** access token receives. Those come from the signed-in user's RBAC on that resource server (then optionally narrowed by `requireAuthorizationScopesInRequest`).

**Client credential roles and permissions** (Access Roles with `isClientRole`) apply only to the **client credentials** grant. They are not intersected into a user access token. Enabling them on an app does not cap what a logged-in user can do through that app.

If two applications must call the same physical API but with different privilege for the same user, use separate resource servers (distinct identifiers) and authorize on `aud`, or enforce `azp` (authorized party / client) in the API. Do not expect client-credential roles to create that split. See [Scope resolution and token issuance](https://github.com/Authifi/idbroker/blob/main/packages/auth/docs/oidc/scope-resolution-and-token-issuance.md#user-tokens-vs-client-credential-roles).

## Supported scopes (scopes_supported)

These scopes are advertised by each deployment's OpenID Provider discovery document:

`https://{host}/_api/auth/{tenantId}/.well-known/openid-configuration`

Use your issuer's discovery document as the authoritative `scopes_supported` list; the table below covers the common login scopes plus GA4GH/RAS-related scopes wired in the provider.

| Scope                  | Type                    | What you get                                                                                                                                                                                                                                                                                                                                                                              | Notes                                                                                                                                                 |
| ---------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openid`               | Standard OIDC           | Enables OpenID Connect; returns subject identifier (sub) and allows ID token issuance.                                                                                                                                                                                                                                                                                                    | Required for OIDC login.                                                                                                                              |
| `offline_access`       | Standard OIDC           | Requests a refresh token (when the flow and client type allow it).                                                                                                                                                                                                                                                                                                                        | Typically used with authorization_code.                                                                                                               |
| `iss`                  | Standard OIDC extension | Requests issuer (iss) claim where supported.                                                                                                                                                                                                                                                                                                                                              | This provider also supports an authorization response iss parameter.                                                                                  |
| `address`              | Standard OIDC           | Requests address claim (and related address sub-claims).                                                                                                                                                                                                                                                                                                                                  | See claims_supported: address.                                                                                                                        |
| `email`                | Standard OIDC           | Requests email and email_verified claims.                                                                                                                                                                                                                                                                                                                                                 | See claims_supported: email, email_verified.                                                                                                          |
| `phone`                | Standard OIDC           | Requests phone_number and phone_number_verified claims.                                                                                                                                                                                                                                                                                                                                   | See claims_supported: phone_number, phone_number_verified.                                                                                            |
| `profile`              | Standard OIDC           | Requests basic profile claims like name, given_name, family_name, picture, locale, etc. Also unlocks role claims under this scope: default clients receive `resource_roles` (roles keyed by resource-server identifier). Flattened `access_roles` / `roles` claims appear only when the client has legacy role compatibility enabled — there is no separate `access_roles` request scope. | Prefer `resource_roles` for new integrations.                                                                                                         |
| `external_groups`      | Custom                  | Requests external_groups claim (typically groups from external IdPs).                                                                                                                                                                                                                                                                                                                     | Useful for RBAC mapping.                                                                                                                              |
| `tenant`               | Custom                  | Requests tenant claim (tenant context / tenant metadata).                                                                                                                                                                                                                                                                                                                                 | Useful for multi-tenant apps.                                                                                                                         |
| `groups`               | Custom                  | Requests groups claim (internal group memberships).                                                                                                                                                                                                                                                                                                                                       | Distinct from external_groups.                                                                                                                        |
| `identity_provider`    | Custom                  | Requests identity_provider claim (IdP details for the session/user).                                                                                                                                                                                                                                                                                                                      | Useful for debugging and UX.                                                                                                                          |
| `linked_identity`      | Custom                  | Requests linked_identity claim (linked accounts / identities).                                                                                                                                                                                                                                                                                                                            | Useful for account linking views.                                                                                                                     |
| `amr`                  | OIDC                    | Requests amr claim (Authentication Methods References per [RFC 8176](https://datatracker.ietf.org/doc/html/rfc8176)). Emitted values can include `pwd` (local password), `otp` (TOTP), `hwk` (hardware-bound WebAuthn / FIDO2), `swk` (software-backed / synced WebAuthn), `sc` (PIV / smart card), and `mfa` (aggregate or legacy-projected second-factor marker).                       | Do not allow-list only a subset of these values. Depending on claims-profile settings, precise factors such as `sc`/`swk` may project to `mfa`/`hwk`. |
| `custom_claims`        | Custom                  | Requests custom_claims claim (tenant- or client-defined claims) on interactive UserInfo.                                                                                                                                                                                                                                                                                                  | Exact content depends on configuration. See [Using `custom_claims`](#using-custom_claims).                                                            |
| `provider_claims`      | Custom                  | Requests provider_claims claim (raw/normalized claims from upstream provider).                                                                                                                                                                                                                                                                                                            | Use sparingly due to size/privacy.                                                                                                                    |
| `ga4gh_passport_v1`    | Custom                  | Requests a GA4GH Passport (`ga4gh_passport_v1`) claim assembled for UserInfo when passport issuance is enabled for the tenant.                                                                                                                                                                                                                                                            | Used with GA4GH visa / passport workflows.                                                                                                            |
| `federated_identities` | Custom                  | Requests a RAS-compatible `federated_identities` claim describing linked / federated identities.                                                                                                                                                                                                                                                                                          | Useful for RAS and multi-IdP account linkage.                                                                                                         |

## How to request scopes

### 1 Authorization Code Flow

Send the user to the authorization endpoint with `response_type=code` and a space-separated scope list.

```http
GET /_api/auth/{tenantId}/authorize?
  client_id=YOUR_CLIENT_ID
  &redirect_uri=https%3A%2F%2Fyourapp.example%2Fcallback
  &response_type=code
  &scope=openid%20profile%20email%20offline_access%20groups
  &code_challenge=BASE64URL(SHA256(verifier))
  &code_challenge_method=S256
  &state=RANDOM
  &nonce=RANDOM
```

Exchange the authorization code at the token endpoint to obtain tokens (access_token, id_token, and optionally refresh_token).

```http
POST https://{host}/_api/auth/{tenantId}/oidc/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=AUTH_CODE
&redirect_uri=https%3A%2F%2Fyourapp.example%2Fcallback
&client_id=YOUR_CLIENT_ID
&code_verifier=YOUR_PKCE_VERIFIER
```

### 2 Calling UserInfo to read scoped claims

```http
GET https://{host}/_api/auth/{tenantId}/me
Authorization: Bearer ACCESS_TOKEN
```

If you requested profile/email/phone/address or custom scopes like groups/`custom_claims`, the corresponding claims may appear in the UserInfo response (for example `resource_roles` via `profile`).

### 3 Refresh tokens (offline_access)

```http
POST https://{host}/_api/auth/{tenantId}/oidc/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token
&refresh_token=REFRESH_TOKEN
&client_id=YOUR_CLIENT_ID
```

### 4 Device Authorization Flow (for CLIs and TVs)

```http
POST https://{host}/_api/auth/{tenantId}/device/auth
Content-Type: application/x-www-form-urlencoded

client_id=YOUR_CLIENT_ID
&scope=openid%20profile%20email%20groups
```

Poll the token endpoint with `grant_type=urn:ietf:params:oauth:grant-type:device_code` using the returned device_code, until you receive tokens.

### 5 Client Credentials (service-to-service)

Client Credentials is listed as supported. Note that OIDC identity / UserInfo claims generally do not apply because there is no end-user. In particular, requesting `custom_claims` on a client-credentials token does **not** produce the nested UserInfo `custom_claims` object described later in this guide — that path is for interactive user tokens.

```http
POST https://{host}/_api/auth/{tenantId}/oidc/token
Content-Type: application/x-www-form-urlencoded
Authorization: Basic BASE64(client_id:client_secret)

grant_type=client_credentials
&scope=mock%3Aapi%3Aread
&resource=https%3A%2F%2Fapi.example%2Fx
```

Only request scopes that make sense for a client-only token (typically resource / API scopes). Avoid `openid` and other user-identity scopes unless your deployment explicitly supports them for client credentials.

**Scope narrowing for service tokens:** with the default `requireAuthorizationScopesInRequest=false`, the issuer may still auto-grant **all** Resource Server Permission scopes assigned through the client's Access Roles marked for client credential use (`isClientRole: true`), even if the token request listed only `mock:api:read`. To make the request `scope` actually narrow the issued token, enable **Require Resource Indicator scopes** (`requireAuthorizationScopesInRequest=true`) on that client.

## Using `custom_claims`

`custom_claims` is a custom OIDC scope that unlocks a nested `custom_claims` object in the UserInfo response. The keys and values inside that object are defined by your tenant, client, and identity-provider configuration — they are not a fixed standard claim set.

### Request the scope

Include `custom_claims` in the authorize `scope` parameter (alongside `openid`):

```http
GET /_api/auth/{tenantId}/authorize?
  client_id=YOUR_CLIENT_ID
  &redirect_uri=https%3A%2F%2Fyourapp.example%2Fcallback
  &response_type=code
  &scope=openid%20profile%20email%20custom_claims
  &code_challenge=BASE64URL(SHA256(verifier))
  &code_challenge_method=S256
  &state=RANDOM
  &nonce=RANDOM
```

Without the `custom_claims` scope, the `custom_claims` claim is filtered out of UserInfo even if values exist for the user.

### Read claims from UserInfo

After exchanging the authorization code for tokens, call UserInfo with the access token:

```http
GET https://{host}/_api/auth/{tenantId}/me
Authorization: Bearer ACCESS_TOKEN
```

Example response shape:

```json
{
  "sub": "user-subject-id",
  "email": "user@example.com",
  "custom_claims": {
    "eye_color": "hazel",
    "favorite_cat": "Scottish Fold"
  }
}
```

Prefer UserInfo over the ID token for these claims. Custom claims are resolved when UserInfo is called; do not assume they are embedded in the ID token.

### Where the values come from

Two common sources populate `custom_claims`:

1. **Identity provider / profile mapping** — An IdP profile script (or claim mapping) returns a `custom_claims` object. Those values are stored on the user and projected into UserInfo when the scope is requested.
2. **Client UserInfo customization script** — If the OIDC client has a `customizeUserInfoClaims` script configured, that script's return value is assigned to the UserInfo `custom_claims` object (replacing the mapped values for that response).

Exact keys depend on how administrators configured the tenant, client, and IdP. Use the Auth admin UI (or claims preview APIs, where available) to inspect what a given client will emit.

### Practical notes

- Treat `custom_claims` as application-specific data. For role-based authorization, prefer `resource_roles` (via `profile`) and `groups` when those fit your model.
- Cache authorization decisions server-side when needed; do not rely only on client-side inspection of UserInfo.
- `custom_claims` is for interactive user tokens and UserInfo. Do not expect the nested UserInfo `custom_claims` object from client-credentials tokens.

## Guidance on the custom scopes

For custom scopes, treat the resulting claims as authorization-sensitive data. In practice:

- Prefer `resource_roles` (via `profile`) and `groups` for authorization logic. Flattened `access_roles` claims require legacy role compatibility and are not unlocked by a separate `access_roles` scope. Use `provider_claims` only for troubleshooting or migration.
- Cache authorization decisions server-side and re-check periodically; do not hardcode client-side role checks as your only gate.
- Do not rely on ID tokens for authorization decisions. Use access tokens or server-side checks. Fetch them from UserInfo or a dedicated API if payloads get big.

## References

- **OpenID Connect Core 1.0 specification** (defines how scopes like openid, profile, email, address, and phone work, and how claims are returned): https://openid.net/specs/openid-connect-core-1_0.html
- **OpenID Connect Discovery 1.0** (defines the discovery document and how clients find an OP's endpoints): https://openid.net/specs/openid-connect-discovery-1_0.html
- **OAuth 2.0 Authorization Framework (RFC 6749)** (defines authorization grants, use of scope, and core OAuth 2 flows): https://datatracker.ietf.org/doc/html/rfc6749
- **OAuth 2.0 Bearer Token Usage (RFC 6750)** (defines how access tokens are presented to protected APIs like UserInfo): https://datatracker.ietf.org/doc/html/rfc6750

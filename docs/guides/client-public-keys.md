---
title: Application public keys
---

<!-- Generated from idbroker: packages/auth/docs/oidc/client-public-keys.md. Edit it there, not in Authifi/docs. -->

# Application public keys

These keys belong to the application. Authifi uses them for one check: when the application authenticates with `private_key_jwt`, it signs a short-lived JWT with its private key, and Authifi verifies that signature.

The application keeps the private key. Authifi stores the matching public key, or a URL where it can fetch that public key. Changing either one changes which signatures Authifi trusts.

`private_key_jwt` is a token-endpoint authentication method. The application uses it when it redeems an authorization code, refreshes a token, or requests a client-credentials token. The machine-to-machine wizard does not offer this method. On the full application form, set **Token endpoint authentication method** to **Private Key JWT**, then register a public key or a JWKS URI as described below.

These keys are separate from:

- **Authifi's token-signing keys.** Those sign the tokens Authifi issues. They are published at `GET /auth/{tenantId}/.well-known/jwks.json` and rotated with the tenant JWK routes.
- **The DPoP key.** That key proves who is using a token. Keep it different from this authentication key. See [Client credentials with DPoP](./client-credentials-dpop.md).
- **The client secret.** `client_secret_jwt`, `client_secret_post`, and `client_secret_basic` use the client secret. They do not use this public key.

The Admin UI **Test login** flow cannot sign a `private_key_jwt` assertion, so it cannot finish login for an application that uses this method.

## Use one source

Register either a public key stored on the application, or a JWKS URI. Do not set both. Authifi refuses to load an application that has both, so its token requests fail until one of them is removed.

Clear the JWKS URI when you register a stored key. The key routes below replace the stored public key and leave the URI unchanged. An application save cannot remove a stored public key. It can set or clear the URI.

## JWKS URI

On a web or native application, **JWKS URI** is the URL of the application's public key set. Authifi fetches that document when it checks a signature.

- Saving a non-empty value sets the URL.
- Clearing the field and saving removes the URL.
- A URL entered in the Config editor is copied onto this field. The save uses the field, not a second copy in the JSON.

Through the API, send `jwksUri` on the application, or `config.jwksUri`.

- **Update** (`PATCH`): omitting the URI leaves the stored URL unchanged. Send `null` to clear it.
- **Replace** (`PUT`): omitting both `jwksUri` and `config.jwksUri` clears the URL. A tenant administrator's replace of a system application does not clear it.

A tenant administrator cannot change this URL on a system application (the management client, the other built-in clients, or the portal BFF). A system administrator can.

## Stored public key

A normal application save does not change the stored public key. A `jwks` value on create, update, or replace is ignored. Change the stored key only with the routes below. Each call can include `expectedVersion` so it succeeds only when the application's stored version still matches.

Generate a key pair:

```http
POST /auth/admin/tenants/{tenantId}/clients/{id}/jwk
```

Authifi stores the public key and returns the private key once, as PEM, with the public JWK and the application's new version. Store that private key on the application. Authifi does not keep it. This call requires `auth.clients.jwk_create` or permission to update applications. It does not change the JWKS URI.

Register a public key the application already has:

```http
PATCH /auth/admin/tenants/{tenantId}/clients/{id}/jwk
```

Send one public JWK. Authifi replaces the stored key with that JWK. Send the public key only. This call requires `auth.clients.jwk_update` or permission to update applications. It does not change the JWKS URI.

A tenant administrator cannot generate or replace the key on the portal BFF or the console BFF. Other system applications, including the management client, accept these calls when the caller has the required scope. A system administrator can change any of them.

## Where the values are stored

The public key and the URI are fields on the application (`jwks` and `jwksUri`), not ordinary application settings. That is why a save of the rest of the application cannot replace the public key.

During the move to those fields, Authifi still writes a copy under `config`. That copy is deprecated. While it exists, a `jwks` or `jwksUri` property present on `config` is the value Authifi uses, including an explicit `null`, which clears it. When `config` has no such property, Authifi uses the application field. Change the public key through the JWK routes and the URI through `jwksUri`. A later release will stop writing the `config` copy.

## See also

- [How to start login](./login-initiation.md) — redeeming an authorization code with a client assertion
- [Client credentials with DPoP](./client-credentials-dpop.md) — a different key, used to bind a token to its caller

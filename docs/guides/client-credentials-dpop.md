---
title: Client credentials with DPoP
---

<!-- Generated from idbroker: packages/auth/docs/oidc/client-credentials-dpop.md. Edit it there, not in Authifi/docs. -->

# Client credentials with DPoP

**Force DPoP** is an application setting for machine-to-machine access. When it is on, a client-credentials access token is bound to a signing key the client holds. Each token request and each API call includes a fresh proof of that key. A copied access token fails at an API that checks that proof.

The setting is off by default. Existing machine clients keep receiving ordinary bearer tokens until an administrator turns it on for an application whose software can sign proofs.

## What DPoP is

[DPoP](https://www.rfc-editor.org/rfc/rfc9449) (Demonstrating Proof of Possession, RFC 9449) is an OAuth proof that the caller still holds a private key.

The client signs a short-lived JWT, the DPoP proof, and sends it in the `DPoP` HTTP header. The proof's header carries the public key. Authifi checks the signature and records the proof's unique id. The access token then includes `cnf.jkt`, the thumbprint of that public key, and `token_type` is `DPoP`.

Later, the API call sends the access token as `Authorization: DPoP` plus a new proof. That proof is for this HTTP method and this URL, and it includes a hash of the access token. The public key in the new proof must have the same thumbprint as `cnf.jkt`.

The client-credentials grant itself is unchanged: the client still authenticates to the token endpoint with its client id and a confidential method (`client_secret_post`, `client_secret_basic`, `client_secret_jwt`, or `private_key_jwt`), and the token still represents the application. Audience and scopes still come from the `resource` parameter and the application's client-credential roles. See [Request Scopes](./oidc-request-scopes.md) for how resource scopes are issued.

DPoP is a second secret. The client secret (or the `private_key_jwt` key) proves who is asking for a token. The DPoP key proves who is using the token. Keep those keys separate so one leak does not cover both jobs.

## What Force DPoP does

**Force DPoP** is stored on the application as `config.forceDPoP`. It applies to every grant on that application.

| Force DPoP            | Token request         | Access token                                       |
| --------------------- | --------------------- | -------------------------------------------------- |
| Off, no `DPoP` header | Succeeds              | `token_type: Bearer`, no `cnf.jkt`                 |
| Off, valid proof      | Succeeds              | `token_type: DPoP`, `cnf.jkt` set to the proof key |
| On, valid proof       | Succeeds              | `token_type: DPoP`, `cnf.jkt` set to the proof key |
| On, proof missing     | `400` `invalid_grant` | No token                                           |

Sending a valid proof binds the token even while the switch is off. After that, API calls with the token use `Authorization: DPoP` and a new proof. A bearer presentation of a bound token is rejected.

One switch covers the whole application. An application that both signs users in and calls APIs with client credentials requires a proof for both when the switch is on. Use two applications when user login should stay on bearer tokens and the machine client should use DPoP.

Turn Force DPoP on only after the client can sign proofs. Turning it on earlier makes that application's token requests fail. The tenant NHE field `settings.nhe.requireDpop` is a different setting and leaves this application switch unchanged.

Client credentials does not issue a refresh token. Each new access token is a new token request with a new proof.

## Turn Force DPoP on

1. Open **Application and API settings** → **App Dashboard**.
2. Create or edit the machine application.
   - **+ ADD NEW** → **Machine-to-machine** shows **Force DPoP** on the wizard, next to the client authentication method.
   - The full application form shows **Force DPoP** for web and native applications.
3. Turn **Force DPoP** on. A dialog asks whether the application already has a signing key on the machine that will call Authifi.

| Choice                           | What happens                                                                                  |
| -------------------------------- | --------------------------------------------------------------------------------------------- |
| **Not now**                      | The switch stays off.                                                                         |
| **It already has a key**         | The switch turns on. Keep that private key on the client machine. There is nothing to upload. |
| **Create a key and download it** | The browser creates an ES256 (P-256) key and downloads `dpop-private-key.jwk.json`.           |

4. Save the application. The switch is stored only after save.

The downloaded file is a JSON Web Key. The `d` field is the private key:

```json
{
  "kty": "EC",
  "crv": "P-256",
  "alg": "ES256",
  "x": "<public>",
  "y": "<public>",
  "d": "<private>"
}
```

The key is created in the administrator's browser. Authifi's servers never receive it, and Authifi cannot return it later. The download is the only copy. Put the file on the machine that calls Authifi, in a secret store that only that process can read. Keep it out of source control, container images, logs, and tickets.

**Reset key** (shown on the machine-to-machine wizard while the switch is on, and on the full form when the application has the client-credentials grant) downloads a new file the same way. Put that file on the client and sign the next token request with it. The next access token is bound to whichever key signs that request. Access tokens already issued stay bound to the key that signed them until they expire. Resetting downloads a new key in the browser. It does not delete the previous file, and a client that still signs with the previous key still receives tokens bound to that key.

A key the application already has may use `ES256`, `Ed25519`, or `EdDSA`. Those are the proof algorithms Authifi accepts. Confirm the live list in discovery:

```http
GET /_api/auth/{tenantId}/.well-known/openid-configuration
```

Read `dpop_signing_alg_values_supported`. The file from **Create a key and download it** is always `ES256` on curve `P-256`.

## Ask for a token

Send `POST /_api/auth/{tenantId}/oidc/token` with `grant_type=client_credentials`, the client credentials, and a `DPoP` proof. `{tenantId}` is the tenant identifier (for example `ls`), and the host is your Authifi API host. Paths are under the API base, usually `/_api`.

The proof is a JWT with three parts. The header is:

```json
{
  "typ": "dpop+jwt",
  "alg": "ES256",
  "jwk": {
    "kty": "EC",
    "crv": "P-256",
    "x": "<public>",
    "y": "<public>"
  }
}
```

Put only the public key in `jwk`. Omit `d`.

The payload for the token request is:

| Claim | Value                                                                                                             |
| ----- | ----------------------------------------------------------------------------------------------------------------- |
| `htm` | `POST`                                                                                                            |
| `htu` | The token endpoint URL, with query and fragment removed. Example: `https://a.example.com/_api/auth/ls/oidc/token` |
| `jti` | A new unique string for this proof                                                                                |
| `iat` | The current time, in seconds. Authifi accepts a proof issued within 300 seconds of its clock                      |

Omit `nonce`. Authifi does not issue DPoP nonces, and a proof that contains `nonce` is rejected.

```http
POST /_api/auth/ls/oidc/token
Content-Type: application/x-www-form-urlencoded
DPoP: <proof>

grant_type=client_credentials&client_id=billing-job&client_secret=<secret>&resource=https://api.example.com
```

A successful response looks like this:

```json
{
  "access_token": "<jwt>",
  "token_type": "DPoP",
  "expires_in": 3600
}
```

`token_type` in that JSON body is `DPoP`. Inside the access token JWT, the binding is `cnf.jkt`, the thumbprint of the proof's public key:

```json
{
  "cnf": { "jkt": "<thumbprint of the proof public key>" }
}
```

Use a new `jti` on every token request. The same key signs proofs for the life of that access token. A later token request may use a new key; that new token is bound to the new thumbprint.

## Call an API

Send the access token with the `DPoP` scheme, and send a new proof:

```http
GET https://api.example.com/v1/invoices
Authorization: DPoP <access_token>
DPoP: <new proof>
```

Authifi APIs use the same two headers. For example, `GET /_api/auth/admin/tenants/{tenantId}/secrets/{secretId}`.

The new proof uses the same key. Its claims are:

| Claim | Value                                                                                    |
| ----- | ---------------------------------------------------------------------------------------- |
| `htm` | The API method, such as `GET`                                                            |
| `htu` | The public URL of the API request, with query and fragment removed                       |
| `ath` | Base64url-encoded SHA-256 of the ASCII access token                                      |
| `jti` | A new unique string. A `jti` already used at the token endpoint or at an API is rejected |
| `iat` | Within 300 seconds                                                                       |

`htu` is the URL the caller uses on the public network. An in-cluster host name does not match.

Authifi APIs check the proof before they apply the token's scopes. These requests are rejected with `401` and a `WWW-Authenticate: DPoP` header:

- `Authorization: Bearer` for a token that has `cnf.jkt`
- A missing proof, a thumbprint that does not match `cnf.jkt`, or a proof whose `htm`, `htu`, or `ath` does not match this request
- A repeated `jti`
- A token that carries a certificate thumbprint (`cnf["x5t#S256"]`)

An ordinary bearer token, one with no `cnf.jkt`, is unchanged.

## Protect your own API

An API that validates Authifi access tokens has the same job. The access token JWT carries `cnf.jkt`, the thumbprint the proof must match. Accepting that JWT as a normal bearer token drops the constraint.

`@authifi/services-auth` performs the check on routes that already require a JWT, after two bindings are in place:

- `AuthenticationBindings.DPOP_REPLAY_CHECK` records each proof id. The function receives `{ clientId, jti, iat }` and returns `true` the first time that id is seen and `false` when it was already used. A token with `cnf.jkt` is rejected when this callback is missing. Tokens without `cnf.jkt` still work.
- `AuthenticationBindings.DPOP_RESOURCE_ORIGIN` returns the public origin of the API, such as `https://api.example.com`. The expected `htu` is that origin plus the request path.

Authifi's own APIs bind both. A service that validates tokens itself, without `@authifi/services-auth`, implements the same checks: signature, algorithm, `typ` of `dpop+jwt`, embedded public key, `iat` window, single-use `jti`, matching `htm` / `htu` / `ath`, and matching `cnf.jkt`.

## Errors you will see

Token-endpoint failures are HTTP 400.

| Situation                                         | `error`              | `error_description`                                                                   |
| ------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------- |
| Force DPoP is on and the `DPoP` header is missing | `invalid_grant`      | `grant request is invalid: DPoP proof JWT not provided`                               |
| This `jti` was already used                       | `invalid_grant`      | `grant request is invalid: DPoP proof JWT Replay detected`                            |
| Bad signature, `typ`, or algorithm                | `invalid_dpop_proof` | `invalid DPoP key binding`                                                            |
| `iat` is missing or older than 300 seconds        | `invalid_dpop_proof` | `DPoP proof must have a iat number property` or `DPoP proof iat is not recent enough` |
| The proof includes `nonce`                        | `invalid_dpop_proof` | `DPoP nonces are not supported`                                                       |
| `htm` does not match the method                   | `invalid_dpop_proof` | `DPoP proof htm mismatch`                                                             |
| `htu` is missing or is not this endpoint          | `invalid_dpop_proof` | `DPoP proof htu mismatch`                                                             |

A wrong client secret is still `invalid_client`, including when the proof is valid.

API failures are HTTP 401. The `WWW-Authenticate` header names the reason, for example `DPoP proof JWT not provided`, `DPoP proof htm mismatch`, `DPoP proof htu mismatch`, `DPoP proof ath mismatch`, `failed jkt verification`, or `DPoP proof JWT Replay detected`. The header also lists the accepted algorithms (`ES256`, `Ed25519`, `EdDSA`).

## Sample application

Authifi maintains a sample application that requests a client-credentials token with DPoP and calls an API with a proof. The sample is the supported way to see a working client, including key loading, proof signing, and the API call.

The sample is not published for anonymous download. [Contact Authifi](https://www.authifi.com/contact-us) and ask Authifi Developers for access to the DPoP client-credentials sample so you can run it against your tenant.

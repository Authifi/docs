---
title: Identity provider profile scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/map-user-profile.md. Edit it there, not in Authifi/docs. -->

# Identity provider profile scripts

Identity provider profile scripts map provider claims onto the user profile Auth validates and stores. This reference covers both **Map User Profile** and OAuth **Fetch User Profile** scripts because they use the same runner and context.

## Function contract

```js
export default async function (ctx) {
    return {
        email: ctx.secrets.claims.email,
        given_name: ctx.secrets.claims.given_name,
        family_name: ctx.secrets.claims.family_name,
    };
}
```

Return a user profile object. Common fields include `email`, `given_name`, `family_name`, `name`, `nickname`, and `preferred_username`. Provider login fails when the result does not satisfy the provider's required profile validation. You may instead declare two parameters and call `callback(null, profile)`.

Profile scripts have a three-second execution limit.

## Context

| Member                                       | Description                                                                      |
| -------------------------------------------- | -------------------------------------------------------------------------------- |
| `ctx.secrets.claims`                         | Claims from the provider. The exact fields depend on the provider strategy.      |
| `ctx.secrets.accessToken`                    | Provider access token, when the login produced one.                              |
| `ctx.secrets.accessTokenPayload`             | Decoded provider access-token payload, when available.                           |
| `ctx.secrets.idTokenPayload`                 | Decoded provider ID-token payload, when available.                               |
| `ctx.tenantId`                               | Numeric tenant ID, when the strategy supplies the Auth application context.      |
| `await ctx.getTenantVar(name, isSensitive?)` | Reads a tenant variable when the strategy supplies the Auth application context. |

`ctx.getUserSecret` is not available because the Auth user does not yet exist in this script context. Job fields and helpers such as `ctx.job`, `ctx.getSecret`, `saveReport`, and key-manager maintenance functions are also unavailable.

## Globals

| Global                                              | Description                                                          |
| --------------------------------------------------- | -------------------------------------------------------------------- |
| `callback(error, result)`                           | Completes a two-parameter script.                                    |
| `log(message, ...args)`                             | Writes to the server log with a QuickJS prefix.                      |
| `logger.info\|warn\|error\|debug(message, ...args)` | Writes to the corresponding server log level.                        |
| `await jobLog(message)`                             | Writes to the server log; it does not create a job-log row.          |
| `await fetch(url, init?)`                           | HTTP client returning `{ status, statusText, data }`.                |
| `axios.get\|post\|put\|patch\|delete(...)`          | Deprecated compatibility HTTP API. Prefer `fetch`.                   |
| `await generateExcel(worksheets)`                   | Generates an xlsx workbook and returns its bytes as a base64 string. |
| `btoa(text)` / `atob(encoded)`                      | Base64 encoding and decoding.                                        |

## HTTP and runtime behavior

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject. Profile scripts do not receive an automatic client-credentials token.

The only supported module import is `ipaddr.js`. QuickJS has an 8 MB memory limit. Data copied to `ctx` is serialized: functions are omitted, dates become UTC strings, and buffers become base64 strings.

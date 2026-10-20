---
title: API access-token claims scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/token-claims.md. Edit it there, not in Authifi/docs. -->

# API access-token claims scripts

An API custom-claims script runs when Auth issues an access token for that API and a tenant user exists. Returned fields are merged into the token.

## Function contract

```js
export default async function (ctx) {
    return {
        department: await ctx.getUserSecret('department'),
    };
}
```

Return an object of claim names to values. Existing token keys are not overwritten. You may instead declare two parameters and call `callback(null, claims)`.

The following fields are always ignored:

- `org.labshare.nhe.bootstrap_verified`
- `org.labshare.nhe.delegation_verified`
- `org.labshare.nhe.delegation_issuance_pending`

For NHE delegation tokens, `scope` and `aud` are also ignored.

Access-token claims scripts have a three-second execution limit.

## Context

| Member                                       | Description                                                               |
| -------------------------------------------- | ------------------------------------------------------------------------- |
| `ctx.secrets.user`                           | `{ id, email, familyName, givenName, middleName }` for the token subject. |
| `ctx.secrets.claims`                         | An empty object. User fields are under `ctx.secrets.user`.                |
| `ctx.tenantId`                               | Numeric tenant ID.                                                        |
| `ctx.namespaceId`                            | API namespace ID or `null`.                                               |
| `await ctx.getTenantVar(name, isSensitive?)` | Reads a tenant or namespace variable.                                     |
| `await ctx.getUserSecret(name)`              | Reads the current user's secret and returns its plaintext value.          |

Job fields and helpers such as `ctx.job`, `ctx.getSecret`, `saveReport`, and key-manager maintenance functions are not available.

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

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject. Token scripts do not receive an automatic client-credentials token.

The only supported module import is `ipaddr.js`. QuickJS has an 8 MB memory limit. Data copied to `ctx` is serialized: functions are omitted, dates become UTC strings, and buffers become base64 strings.

---
title: Client authorization scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/authorization.md. Edit it there, not in Authifi/docs. -->

# Client authorization scripts

An authorization script runs after login for a client and decides whether the user may continue. Tenant administrators bypass this script.

## Function contract

```js
export default async function (ctx) {
    return ctx.secrets.groups.includes('Researchers');
}
```

Return `true` to allow login. `false` or any other falsy value rejects login with HTTP 403. You may instead declare two parameters and call `callback(null, result)`; a truthy first callback argument fails the script.

Authorization scripts have a three-second execution limit.

## Context

| Member                                       | Description                                                            |
| -------------------------------------------- | ---------------------------------------------------------------------- |
| `ctx.secrets.user`                           | Plain user record, including `id`, `email`, and profile fields.        |
| `ctx.secrets.connection`                     | Identity-provider connection name used for this login, when available. |
| `ctx.secrets.ipAddress`                      | Request IP address.                                                    |
| `ctx.secrets.groups`                         | Array of group names. Use `includes()` to check membership.            |
| `ctx.tenantId`                               | Numeric tenant ID.                                                     |
| `ctx.namespaceId`                            | Client namespace ID or `null`.                                         |
| `await ctx.getTenantVar(name, isSensitive?)` | Reads a tenant or namespace variable.                                  |
| `await ctx.getUserSecret(name)`              | Reads the current user's secret and returns its plaintext value.       |

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

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject. Login scripts do not receive an automatic client-credentials token.

The only supported module import is `ipaddr.js`. QuickJS has an 8 MB memory limit. Data copied to `ctx` is serialized: functions are omitted, dates become UTC strings, and buffers become base64 strings.

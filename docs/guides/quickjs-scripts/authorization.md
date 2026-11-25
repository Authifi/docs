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

| Global                                              | Description                                                                                     |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `callback(error, result)`                           | Completes a two-parameter script.                                                               |
| `log(message, ...args)`                             | Writes to the server log with a QuickJS prefix.                                                 |
| `logger.info\|warn\|error\|debug(message, ...args)` | Writes to the corresponding server log level.                                                   |
| `await jobLog(message)`                             | For compatibility with job scripts. Writes to the server log when used outside of a job script. |
| `await fetch(url, init?)`                           | HTTP client returning `{ status, statusText, data }`.                                           |
| `axios.get\|post\|put\|patch\|delete(...)`          | Deprecated compatibility HTTP API. Prefer `fetch`.                                              |
| `await generateExcel(worksheets)`                   | Generates an xlsx workbook and returns its bytes as a base64 string.                            |
| `btoa(text)` / `atob(encoded)`                      | Base64 encoding and decoding.                                                                   |

## HTTP and runtime behavior

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject. Login scripts do not receive an automatic client-credentials token.

- **Imports and evaluation:** only `ipaddr.js` can be imported. `eval` and `Function` constructors are disabled.
- **Memory, stack, and source:** 8 MiB memory limit, 256 KiB stack limit, and 512 KiB script source limit.
- **HTTP calls:** 200 total `fetch` and `axios` requests per run. Up to 10 requests run concurrently; additional concurrent requests queue up to 20 MiB of queued request data, and requests past either cap reject.
- **Payload and result size:** each `ctx` property is limited to 2 MiB. Outgoing HTTP requests, HTTP responses, and the script result are each limited to 2 MiB.
- **Logging:** 200 entries or 64 KiB total per run across `log`, `logger`, and `jobLog`. Extra log output is truncated with one warning.
- **Context serialization:** data copied to `ctx` omits functions, converts dates to UTC strings, and encodes buffers as base64 strings.

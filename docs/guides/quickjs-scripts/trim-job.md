---
title: Trim job scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/trim-job.md. Edit it there, not in Authifi/docs. -->

# Trim job scripts

A trim job script chooses which Auth repositories the job trims. It does not receive Auth application services.

## Function contract

```js
export default async function (ctx) {
    return ['UserRepository', 'JobLogRepository'];
}
```

Return an array of repository names, or declare two parameters and call `callback(null, names)`. Each name is resolved as `repositories.{name}`. Unknown names are skipped. A two-parameter function must invoke `callback` before its time limit.

The job's **Script Execution Time Limit** bounds execution from 1 to 86,400 seconds.

## Context

| Member             | Description                                                                                                                      |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `ctx.job`          | Job record, including `id`, `name`, `description`, `tenantId`, `namespaceId`, `cron`, `jobType`, `enabled`, and `customOptions`. |
| `ctx.basePath`     | Auth server base URL.                                                                                                            |
| `ctx.tenantName`   | Tenant slug. The numeric tenant ID is `ctx.job.tenantId`.                                                                        |
| `ctx.clientSecret` | Plaintext secret for `ctx.job.customOptions.clientId`, when configured.                                                          |

Trim jobs do not provide `ctx.getTenantVar`, `ctx.getSecret`, or `ctx.getUserSecret`.

## Globals

| Global                                              | Description                                                          |
| --------------------------------------------------- | -------------------------------------------------------------------- |
| `callback(error, result)`                           | Completes a two-parameter script.                                    |
| `log(message, ...args)`                             | Writes to the server log with a QuickJS prefix.                      |
| `logger.info\|warn\|error\|debug(message, ...args)` | Writes to the corresponding server log level.                        |
| `await jobLog(message)`                             | Writes to the server log. Trim jobs do not attach a job-log writer.  |
| `await fetch(url, init?)`                           | HTTP client returning `{ status, statusText, data }`.                |
| `axios.get\|post\|put\|patch\|delete(...)`          | Deprecated compatibility HTTP API. Prefer `fetch`.                   |
| `await generateExcel(worksheets)`                   | Generates an xlsx workbook and returns its bytes as a base64 string. |
| `btoa(text)` / `atob(encoded)`                      | Base64 encoding and decoding.                                        |

`saveReport` and the key-manager maintenance functions are not available.

## HTTP behavior

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject.

When a job client is configured, requests receive its client-credentials bearer token unless they already set `Authorization`.

## Run in Sandbox

The sandbox supplies an in-memory `ctx.job` and omits `ctx.clientSecret`. GET requests are live. POST, PUT, PATCH, and DELETE return synthetic HTTP 200 responses without contacting the server.

## Runtime limits

- **Imports and evaluation:** only `ipaddr.js` can be imported. `eval` and `Function` constructors are disabled.
- **Memory, stack, and source:** 8 MiB memory limit, 256 KiB stack limit, and 512 KiB script source limit.
- **HTTP calls:** 200 total `fetch` and `axios` requests per run. Up to 10 requests run concurrently; additional concurrent requests queue up to 20 MiB of queued request data, and requests past either cap reject.
- **Payload and result size:** each `ctx` property is limited to 2 MiB. Outgoing HTTP requests, HTTP responses, and the script result are each limited to 2 MiB.
- **Logging:** 200 entries or 64 KiB total per run across `log`, `logger`, and `jobLog`. Extra log output is truncated with one warning.
- **Context serialization:** data copied to `ctx` omits functions, converts dates to UTC strings, and encodes buffers as base64 strings.

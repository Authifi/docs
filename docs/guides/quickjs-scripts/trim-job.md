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

The only supported module import is `ipaddr.js`. QuickJS has an 8 MB memory limit. Data copied to `ctx` is serialized: functions are omitted, dates become UTC strings, and buffers become base64 strings.

---
title: Generic job scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/generic-job.md. Edit it there, not in Authifi/docs. -->

# Generic job scripts

Generic jobs run custom JavaScript in a QuickJS sandbox on the job schedule. They can call Auth APIs, read tenant variables, create generated reports, and run key-manager maintenance operations.

## Function contract

```js
export default async function (ctx) {
    return { status: 'success', message: 'Job completed' };
}
```

Return `{ status, message? }`, or declare two parameters and call `callback(null, result)`. A two-parameter function must invoke `callback` before its time limit. A truthy first callback argument fails the run.

The job's **Script Execution Time Limit** bounds execution from 1 to 86,400 seconds.

## Context

| Member                                       | Description                                                                                                                      |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `ctx.job`                                    | Job record, including `id`, `name`, `description`, `tenantId`, `namespaceId`, `cron`, `jobType`, `enabled`, and `customOptions`. |
| `ctx.basePath`                               | Auth server base URL.                                                                                                            |
| `ctx.tenantName`                             | Tenant slug. The numeric tenant ID is `ctx.job.tenantId`.                                                                        |
| `ctx.clientSecret`                           | Plaintext secret for `ctx.job.customOptions.clientId`, when configured.                                                          |
| `await ctx.getTenantVar(name, isSensitive?)` | Reads a tenant variable. Namespace variables are preferred when the job has a namespace.                                         |
| `await ctx.getSecret(name, tenant)`          | Reads a Passbolt secret by resource name for a tenant slug.                                                                      |

`ctx.getUserSecret` is not available to job scripts.

## Globals

| Global                                                            | Description                                                                                               |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `callback(error, result)`                                         | Completes a two-parameter script.                                                                         |
| `log(message, ...args)`                                           | Writes to the server log with a QuickJS prefix.                                                           |
| `logger.info\|warn\|error\|debug(message, ...args)`               | Writes to the corresponding server log level.                                                             |
| `await jobLog(message)`                                           | Outside production, appends to the job log. In production and the sandbox, writes only to the server log. |
| `await fetch(url, init?)`                                         | HTTP client returning `{ status, statusText, data }`.                                                     |
| `axios.get\|post\|put\|patch\|delete(...)`                        | Deprecated compatibility HTTP API. Prefer `fetch`.                                                        |
| `await generateExcel(worksheets)`                                 | Generates an xlsx workbook and returns its bytes as a base64 string.                                      |
| `btoa(text)` / `atob(encoded)`                                    | Base64 encoding and decoding.                                                                             |
| `await saveReport({ report, name?, description?, namespaceId? })` | Stores a string report under Generated Reports for this job's tenant.                                     |

Job-only key-manager globals accept an optional `{ dryRun }`:

- `keyManagerTransitDekUnwrap`
- `keyManagerTransitDekWrapBackfill`
- `keyManagerOpenBaoTransitDekUnwrap`
- `keyManagerOpenBaoTransitDekWrapBackfill`
- `keyManagerLocalDekUnwrap`
- `keyManagerLocalDekWrapBackfill`

Unwrap operations return `{ total, unwrapped, failed, errors }`. Wrap operations return `{ total, wrapped, failed, errors }`.

## HTTP behavior

`fetch` and `axios` resolve to `{ status, statusText, data }`; use `response.data`, not `response.json()`. Non-2xx responses reject. Object bodies are sent as JSON unless the content type is form or multipart data.

When a job client is configured, requests receive its client-credentials bearer token unless they already set `Authorization`. The client's assigned client-credential role must contain the required API permissions.

## Run in Sandbox

The sandbox supplies an in-memory `ctx.job`, but does not load the Auth application. It therefore omits `ctx.getTenantVar`, `ctx.getSecret`, `saveReport`, the key-manager functions, and `ctx.clientSecret`. GET requests are live. POST, PUT, PATCH, and DELETE return synthetic HTTP 200 responses without contacting the server.

## Runtime limits

The only supported module import is `ipaddr.js`. QuickJS has an 8 MB memory limit. Data copied to `ctx` is serialized: functions are omitted, dates become UTC strings, and buffers become base64 strings.

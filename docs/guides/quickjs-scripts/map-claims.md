---
title: SAML and WS-Fed claims mapping scripts
---

<!-- Generated from idbroker: packages/ng-auth/src/assets/docs/quickjs-scripts/map-claims.md. Edit it there, not in Authifi/docs. -->

# SAML and WS-Fed claims mapping scripts

A claims mapping script runs while Auth builds a SAML or WS-Fed assertion for a client.

## Function contract

```js
export default async function (ctx) {
    return {
        email: ctx.secrets.claims.userProfileClaims.email,
        groups: ctx.secrets.claims.groups,
    };
}
```

Return an object of claim names to values. A non-empty object replaces the default mapped claims, and Auth adds a name identifier when it is missing. An empty object preserves the default mapping. You may instead declare two parameters and call `callback(null, claims)`.

Claims mapping scripts have a three-second execution limit.

## Context

| Member                                       | Description                                                      |
| -------------------------------------------- | ---------------------------------------------------------------- |
| `ctx.secrets.claims.providerClaims`          | Claims received from the identity provider.                      |
| `ctx.secrets.claims.userProfileClaims`       | Auth user-profile claims.                                        |
| `ctx.secrets.claims.roles`                   | Array of role names.                                             |
| `ctx.secrets.claims.groups`                  | Array of group names.                                            |
| `ctx.secrets.claims.permissions`             | Array of permission names.                                       |
| `ctx.secrets.claims.umrsRoles`               | Array of `{ roleName, resourceServer, resource }`.               |
| `ctx.secrets.claims.umrsRoleGroups`          | Array of `{ groupName, umrsRoles }`.                             |
| `ctx.secrets.user`                           | `{ id }` for the authenticated user.                             |
| `ctx.tenantId`                               | Numeric tenant ID.                                               |
| `ctx.namespaceId`                            | Client namespace ID or `null`.                                   |
| `await ctx.getTenantVar(name, isSensitive?)` | Reads a tenant or namespace variable.                            |
| `await ctx.getUserSecret(name)`              | Reads the current user's secret and returns its plaintext value. |

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

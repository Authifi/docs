# Non-Human Entity (NHE) Delegated Tokens

## Overview

NHE Delegation lets authorized users issue short-lived, narrowly scoped access tokens to Non-Human Entities such as LLM agents, automated pipelines, and AI assistants. NHEs use these tokens to call protected APIs **on behalf of the user**. The tokens identify the machine actor, limit its privileges, and record the delegation in the audit trail.

## Standards

The feature uses these standards and drafts:

- **RFC 8693 (OAuth 2.0 Token Exchange)** — The `actor_token` parameter and `act` JWT claim provide delegation semantics within the existing token exchange grant type.
- **IETF Agent Authorization Profile (AAP) draft (Feb 2026)** — The `agent` structured claim provides agent identity, type, and operator metadata.
- **DPoP (RFC 9449)** — Optionally used for proof-of-possession binding on NHE tokens (when tenant requires it).

## Typical Use Case

1. A researcher is logged in to a web application with an active Authifi session.
2. The application's LLM assistant needs to query the organization's research API.
3. The application exchanges the user's access token for a 5-minute NHE delegation token scoped to `read:articles search:pubmed`.
4. The LLM agent uses this short-lived token to call the API.
5. The API identifies the token as NHE-delegated (via `act` claim) and logs agent activity under the researcher's identity.
6. After 5 minutes, the token expires. The application requests a fresh token if needed.

## Token Exchange Flow

### Request

```
POST /auth/{tenantId}/oidc/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<user's access token JWT>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
actor_token=agent-researcher-01
actor_token_type=urn:authifi:token-type:nhe-id
resource=https://api.example.com
scope=read:articles search:pubmed
```

### Response

```json
{
  "access_token": "<JWT>",
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "token_type": "Bearer",
  "expires_in": 300
}
```

### Error Responses

| Error            | Condition                                                        |
| ---------------- | ---------------------------------------------------------------- |
| `invalid_grant`  | NHE not found, disabled, subject token invalid                   |
| `invalid_scope`  | Requested scopes exceed delegation policy                        |
| `invalid_target` | Resource server not found or rejects NHE tokens                  |
| `access_denied`  | Feature disabled at tenant/client level, or no delegation policy |

## JWT Payload Structure

```json
{
  "iss": "https://auth.example.com/auth/tenant-id",
  "sub": "123",
  "aud": "https://api.example.com",
  "exp": 1710000900,
  "iat": 1710000000,
  "jti": "...",
  "scope": "read:articles search:pubmed",
  "gty": "urn:ietf:params:oauth:grant-type:token-exchange",
  "azp": 42,
  "org.labshare.tenant.name": "my-tenant",
  "org.labshare.tenant.id": 1,
  "act": {
    "sub": "nhe:agent-researcher-01"
  },
  "agent": {
    "id": "agent-researcher-01",
    "type": "llm-autonomous",
    "operator": "org:my-tenant"
  },
  "org.labshare.nhe.delegation": true
}
```

Key claims:

- **`sub`** — The delegating user's identity (the human), not the NHE.
- **`act.sub`** — The NHE actor identity, prefixed with `nhe:` (RFC 8693 delegation semantics).
- **`agent`** — AAP-style agent metadata (id, type, operator, optional model info).
- **`org.labshare.nhe.delegation`** — Boolean marker for resource servers to detect NHE delegation tokens.
- **No `offline_access`** — Refresh tokens are never issued for NHE delegation.

## Configuration

### Tenant Settings

Enable NHE delegation and configure its limits in tenant settings:

```json
{
  "settings": {
    "nhe": {
      "enabled": true,
      "defaultMaxTokenLifetime": 300,
      "hardMaxTokenLifetime": 900,
      "requireDpop": false,
      "allowedNheTypes": [
        "llm-autonomous",
        "llm-assistive",
        "automated-pipeline"
      ],
      "maxNhePerTenant": 50,
      "auditRetentionDays": 90
    }
  }
}
```

| Setting                   | Default       | Description                            |
| ------------------------- | ------------- | -------------------------------------- |
| `enabled`                 | `false`       | Master feature toggle                  |
| `defaultMaxTokenLifetime` | `300`         | Default max TTL (seconds)              |
| `hardMaxTokenLifetime`    | `900`         | Absolute ceiling for any NHE token TTL |
| `requireDpop`             | `false`       | Require DPoP proof-of-possession       |
| `allowedNheTypes`         | `[all three]` | Restrict registrable NHE types         |
| `maxNhePerTenant`         | `50`          | Max NHE registrations per tenant       |
| `auditRetentionDays`      | `90`          | Audit log retention period             |

### Client Settings

Enable NHE delegation on specific clients:

```json
{
  "config": {
    "nhe": {
      "delegationEnabled": true,
      "allowedNheIdentifiers": [],
      "maxTokenLifetime": 300
    }
  }
}
```

### Resource Server Settings

Control NHE token acceptance per resource server:

```json
{
  "config": {
    "nhe": {
      "acceptDelegationTokens": true,
      "requireDelegationTokens": false,
      "allowedNheTypes": ["llm-autonomous"]
    }
  }
}
```

### TTL Resolution

The effective token lifetime is:

```
min(
  tenant.settings.nhe.hardMaxTokenLifetime,
  nhe.maxTokenLifetime,
  policy.maxTokenLifetime (if set),
  client.config.nhe.maxTokenLifetime (if set),
  resourceServer.config.accessToken.lifetime
)
```

Clamped to a minimum of 60 seconds.

## Admin UI

Tenant administrators manage NHE delegation in the Authifi UI:

- **Non-Human Entities** (under the tenant SSO menu) — Register agents, set allowed scopes and token lifetime limits, and configure per-user or per-group delegation policies.
- **NHE Token Audit** (under **Monitoring**) — Review every delegation token issuance, including user, agent, client, resource server, scopes, TTL, and source IP.

Enable the feature at the tenant level (`settings.nhe.enabled`), then enable it on each participating OAuth client and resource server. When `auditRetentionDays` is configured, schedule the **nhe-audit-retention** job under **Resources and Tools** > **Jobs**.

## Management API

### NHE Registration

| Method | Endpoint                                                  | Scope             |
| ------ | --------------------------------------------------------- | ----------------- |
| POST   | `/auth/admin/tenants/{tenantId}/non-human-entities`       | `auth.nhe.create` |
| GET    | `/auth/admin/tenants/{tenantId}/non-human-entities`       | `auth.nhe.list`   |
| GET    | `/auth/admin/tenants/{tenantId}/non-human-entities/{id}`  | `auth.nhe.list`   |
| GET    | `/auth/admin/tenants/{tenantId}/non-human-entities/count` | `auth.nhe.list`   |
| PATCH  | `/auth/admin/tenants/{tenantId}/non-human-entities/{id}`  | `auth.nhe.update` |
| DELETE | `/auth/admin/tenants/{tenantId}/non-human-entities/{id}`  | `auth.nhe.delete` |

### Delegation Policies

| Method | Endpoint                                                  | Scope                      |
| ------ | --------------------------------------------------------- | -------------------------- |
| POST   | `.../non-human-entities/{nheId}/delegation-policies`      | `auth.nhe.policies.create` |
| GET    | `.../non-human-entities/{nheId}/delegation-policies`      | `auth.nhe.policies.list`   |
| GET    | `.../non-human-entities/{nheId}/delegation-policies/{id}` | `auth.nhe.policies.list`   |
| PATCH  | `.../non-human-entities/{nheId}/delegation-policies/{id}` | `auth.nhe.policies.update` |
| DELETE | `.../non-human-entities/{nheId}/delegation-policies/{id}` | `auth.nhe.policies.delete` |

### Token Audit

| Method | Endpoint                                   | Scope                 |
| ------ | ------------------------------------------ | --------------------- |
| GET    | `.../non-human-entities/token-audit`       | `auth.nhe.audit.list` |
| GET    | `.../non-human-entities/token-audit/count` | `auth.nhe.audit.list` |

## Resource Server Integration

### Detecting NHE Tokens

NHE delegation tokens are standard JWTs with additional claims. Resource servers can detect them by checking:

```typescript
// Using the services-auth utility
import {
  getNheDelegationInfo,
  isNheDelegationToken,
} from '@axleresearch/services-auth';

// In your route handler:
if (isNheDelegationToken(req.user)) {
  const info = getNheDelegationInfo(req.user);
  console.log(info.nheIdentifier); // "agent-researcher-01"
  console.log(info.agent?.type); // "llm-autonomous"
}
```

Or directly from the JWT payload:

```typescript
const isNhe = req.user['org.labshare.nhe.delegation'] === true;
const actorSub = req.user.act?.sub; // "nhe:agent-researcher-01"
```

### Security Considerations

- NHE tokens have the same signature and validation as regular access tokens.
- The `sub` claim is the human user; authorization decisions based on `sub` work normally.
- The `act` claim identifies the NHE actor; use this for audit logging and agent-specific restrictions.
- NHE tokens are short-lived (typically 5 minutes) and cannot be refreshed.
- Scopes are always a subset of what the delegating user has.

## Database Schema

NHE delegation uses three tables:

- **`NonHumanEntity`** — Registered NHE agents with allowed scopes and TTL limits.
- **`NheDelegationPolicy`** — Per-user or per-group policies controlling who can delegate to which NHEs.
- **`NheTokenAudit`** — Append-only audit log of every NHE token issuance. Rows are written by the token exchange flow and can only be removed by the `nhe-audit-retention` job (see below); all other mutations throw `405 Method Not Allowed` at the repository level.

## Audit Retention

Audit rows are pruned by the `nhe-audit-retention` scheduled job (`JOB_TYPE.NHE_AUDIT_RETENTION`).

- Runs per tenant and only acts on tenants with `settings.nhe.enabled === true` and a positive `settings.nhe.auditRetentionDays`.
- Deletes `NheTokenAudit` rows whose `issuedAt` is older than `now - auditRetentionDays` via `NheTokenAuditRepository.deleteOlderThan`, the only sanctioned deletion path.
- Batches deletions (default 1000 rows per batch, configurable via `customOptions.batchSize`) and caps at 1,000,000 rows per tenant per run; remaining rows are cleaned up on the next run.
- Tenants without `auditRetentionDays` set, or with a non-positive value, are skipped so operators can opt in explicitly.
- Schedule and enable the job from the admin Jobs dialog like any other scheduled job.

## Security Model

1. **Three-level feature gating** — Tenant, client, and resource server must all permit NHE delegation.
2. **Scope intersection** — NHE tokens can never exceed the delegating user's permissions. Effective scopes are the intersection of user permissions, NHE allowed scopes, and policy allowed scopes.
3. **Short lifetimes** — Default 5 minutes, hard ceiling 15 minutes, no refresh tokens.
4. **Full audit trail** — Every token issuance is recorded with user, NHE, client, resource server, scopes, TTL, and IP address.
5. **Delegation, not impersonation** — The human identity remains in `sub`; the NHE identity is in `act`. This follows RFC 8693 delegation semantics.

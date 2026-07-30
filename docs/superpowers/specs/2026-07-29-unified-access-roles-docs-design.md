# Design: Unified Access Roles documentation (LSA-9677)

**Date:** 2026-07-29  
**Repo:** `authifi-docs`  
**Jira:** [LSA-9677](https://labshare.atlassian.net/browse/LSA-9677)  
**Upstream alignment:** [AxleResearch/auth-monorepo#11589](https://github.com/AxleResearch/auth-monorepo/pull/11589) (merged)  
**Worktree:** `/Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/unified-access-roles-lsa-9677`  
**Branch:** `docs/unified-access-roles-lsa-9677`

## Problem

Public Authifi docs still describe a four-surface authorization model (App Roles / App Permissions / API Roles / API Permissions) and obsolete Client Roles UI. That conflicts with the unified `AccessRole` + `ResourceServerPermission` model, resource-server scoping, and current claim/scope guidance already landed in auth-monorepo.

## Goals

1. Update all **locally owned** `authifi-docs` sources so terminology and behavior match the unified model.
2. Centralize deprecated endpoint and flat-claim detail in one public compatibility appendix.
3. Open a PR referencing LSA-9677 and auth-monorepo #11589, then babysit CI/comments until merge-ready or blocked on a product decision.

## Non-goals

- Editing files synced from auth-monorepo (see below).
- Hand-editing generated `site/` output.
- Rewriting historical `CHANGELOG.md` entries.
- Changing product behavior or auth-monorepo sources from this repo.

## Synced files (do not edit)

From `docs/.authifi-sync.json` — edit upstream only:

| Dest in authifi-docs | Upstream source |
| --- | --- |
| `docs/feature-list.html` | `docs/auth-feature-list.html` |
| `docs/guides/oidc-request-scopes.md` | `packages/auth/docs/oidc/request-scopes.md` |

Role/permission feature-list wording and OIDC scope docs must be corrected in auth-monorepo and pulled via the sync pipeline (for example open sync PR #37).

## Binding terminology and behavior

- **Access Roles** is the standard public term.
- **API Roles** is only a synonym where it helps readers match remaining UI labels or older terminology.
- `AccessRole` is the single role model; `ResourceServerPermission` is the single permission model.
- Roles and permissions are scoped through resource servers.
- Former application/client authorization uses an auto-generated (or linked) resource server associated with the OAuth client.
- **Client permissions** means Resource Server Permissions on an OAuth client's auto-generated placeholder resource server. Define the term before using it.
- Use **client credential roles and permissions** for authorization assigned to applications rather than users.
- New application integrations request `profile` and obtain resource-server-keyed `resource_roles` from trusted UserInfo/session data.
- Ordinary API access tokens are authorized with permission `scope`. Do not imply that `groups` or `resource_roles` are ordinary access-token claims.
- `roles`, `access_roles`, and `enableLegacyRoles` are deprecated compatibility behavior only.
- `access_roles` is not an OIDC request scope.
- Detailed deprecated endpoint and flat-claim behavior lives only in the compatibility appendix; other docs use concise notices and links.

## Approach

Surgical rewrite of audited locally owned sources, plus a new public compatibility appendix adapted from:

`packages/auth/docs/authorization/legacy-roles-permissions-compatibility.md`

in auth-monorepo. Other documents get concise notices and links rather than duplicated tables.

## File plan

### Create

| File | Responsibility |
| --- | --- |
| `docs/authorization/legacy-roles-permissions-compatibility.md` | Public compatibility appendix: deprecated `/roles` and `/permissions` families, legacy relation routes, `enableLegacyRoles` flat claims, migration steps to `profile` + `resource_roles`. Adapted for public docs (Authifi naming, links into this site). |
| Nav entry in `docs/.nav.yml` | Link the appendix under Authorization. |

### Update (high priority)

| File | Change |
| --- | --- |
| `docs/guides/sso-integration-guide.md` | Replace App Roles / App Permissions / API Roles / API Permissions with Access Roles + Resource Server Permissions; distinguish client-linked vs standalone resource-server contexts; fix UserInfo examples (`profile` → `resource_roles`). |
| `docs/guides/users-groups-admin-guide.md` | Remove obsolete Client Roles tab and separate role columns; standardize on Access Roles (API Roles synonym only). |
| `docs/security/recommended-secure-configuration.md` | Replace separate client/API RBAC layers with resource-server contexts over the unified model. |

### Update (medium priority)

| File | Change |
| --- | --- |
| `docs/authorization/super-admin-access.md` | Prefer canonical Access Role and Resource Server Permission APIs; move deprecated endpoint details to the appendix (concise notice + link). |
| `docs/security/security-admin-guide.md` | Describe UMRS grants as resource-server-scoped Access Roles. |
| Changeset / `CHANGELOG.md` | Preserve historical entries. Add consolidation history only if the repository release process requires a changeset for this docs change; do not rewrite old entries. |

### Update (low priority / consistency)

| File | Change |
| --- | --- |
| `docs/authorization/authorization.md` | Align overview language with the unified model; link appendix where legacy behavior is mentioned. |
| `docs/authorization/privileged-access-summary.md` | Replace Client Roles / split-model wording with Access Roles and Resource Server Permissions. |
| Remaining locally owned role/permission prose | Fix obsolete split-model claims only; leave valid generic language alone. |

## Compatibility appendix requirements

- Mirror the substance of the monorepo appendix (deprecated routes, relation replacements, claim compatibility, migration example).
- Use Authifi public product naming and relative links into this site (SSO guide, OIDC scopes page, etc.).
- Do not invent new API contracts beyond upstream source of truth.
- Other pages that formerly embedded wrong claim/scope examples should link here instead of restating tables.

## Validation checklist

1. Search all **locally owned** source docs for: App Role, Application Role, Client Role, API Role, Access Role, App/Client/API/Access Permission, `access_roles`, `resource_roles`, `enableLegacyRoles`, removed Client Roles UI tabs, deprecated endpoint paths.
2. Distinguish valid generic language from obsolete split-model claims.
3. Do not hand-edit generated `site/` when a source file exists; do not edit synced files.
4. Validate links, examples, requested scopes, UserInfo/session claim propagation, formatting; run `mkdocs build` in the worktree.
5. Review the full branch diff for unrelated formatting churn.
6. Create a PR referencing LSA-9677 and auth-monorepo PR #11589.
7. Monitor mergeability, unresolved comments, and CI; fix only valid in-scope issues until merge-ready.

## Delivery

- Branch: `docs/unified-access-roles-lsa-9677`
- PR title/body reference LSA-9677 and https://github.com/AxleResearch/auth-monorepo/pull/11589
- Babysit until merge-ready or explicitly blocked on a product decision

## Success criteria

- Locally owned audited docs teach one Access Role / Resource Server Permission model with resource-server contexts.
- No remaining guidance that `access_roles` is a request scope, or that Client Roles are a separate current entity type (except historical CHANGELOG).
- Deprecated route/claim detail is concentrated in the new appendix.
- Synced files are untouched in this PR.
- PR CI green and review comments triaged.

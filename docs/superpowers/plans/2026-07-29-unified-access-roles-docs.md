# Unified Access Roles Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align locally owned Authifi public docs with the unified Access Roles / Resource Server Permissions model (LSA-9677).

**Architecture:** Add one public compatibility appendix adapted from auth-monorepo, then surgically rewrite audited guides and authorization/security pages so they teach resource-server-scoped Access Roles. Skip files listed in `docs/.authifi-sync.json`. Validate with terminology search + `mkdocs build`.

**Tech Stack:** MkDocs Material, Markdown, Changesets, GitHub PR workflow.

## Global Constraints

- Work only in worktree `/Users/keats.kirsch/Documents/GitHub/authifi-docs-wt/unified-access-roles-lsa-9677` on branch `docs/unified-access-roles-lsa-9677`.
- Standard public term: **Access Roles**. **API Roles** only as synonym for remaining UI/older labels.
- Single models: `AccessRole` and `ResourceServerPermission`, scoped through resource servers.
- Define **client permissions** before use (Resource Server Permissions on an OAuth client's auto-generated placeholder resource server).
- Use **client credential roles and permissions** for authorization assigned to applications rather than users.
- New integrations: request `profile`; read `resource_roles` from trusted UserInfo/session data.
- Ordinary API access tokens: authorize with permission `scope` only — do not imply `groups`/`resource_roles` are ordinary access-token claims.
- `roles`, `access_roles`, `enableLegacyRoles` are deprecated compatibility only; `access_roles` is not an OIDC request scope.
- Do not edit synced files: `docs/feature-list.html`, `docs/guides/oidc-request-scopes.md`.
- Do not hand-edit `site/`. Do not rewrite historical `CHANGELOG.md` entries.
- Detailed deprecated endpoints/flat claims only in the compatibility appendix; other docs get concise notices + links.
- PR must reference LSA-9677 and auth-monorepo PR #11589.

## File map

| Path | Role |
| --- | --- |
| `docs/authorization/legacy-roles-permissions-compatibility.md` | New appendix (canonical deprecated detail) |
| `docs/.nav.yml` | Nav link for appendix |
| `docs/guides/sso-integration-guide.md` | High-priority rewrite |
| `docs/guides/users-groups-admin-guide.md` | High-priority rewrite |
| `docs/security/recommended-secure-configuration.md` | High-priority rewrite |
| `docs/authorization/super-admin-access.md` | Medium — canonical APIs |
| `docs/security/security-admin-guide.md` | Medium — UMRS wording |
| `docs/authorization/authorization.md` | Low — overview consistency |
| `docs/authorization/privileged-access-summary.md` | Low — drop Client Roles split |
| `.changeset/*.md` | Minor changeset for new appendix + significant rewrites |

---

### Task 1: Compatibility appendix + nav

**Files:**
- Create: `docs/authorization/legacy-roles-permissions-compatibility.md`
- Modify: `docs/.nav.yml`

**Interfaces:**
- Consumes: monorepo appendix substance from `packages/auth/docs/authorization/legacy-roles-permissions-compatibility.md`
- Produces: public page path `/authorization/legacy-roles-permissions-compatibility/` for other docs to link

- [ ] **Step 1: Create the appendix**

Adapt monorepo content for Authifi public docs:
- Title: "Roles and Permissions Compatibility"
- Current model summary (Access Roles, Resource Server Permissions, `scope`, `resource_roles`)
- Define client permissions and client credential roles and permissions
- Deprecated `/roles` and `/permissions` tables with canonical replacements (paths relative to `/auth/admin/tenants/{tenantId}`)
- Deprecated relation/legacy views table
- Deprecated claim compatibility (`enableLegacyRoles`, flat `roles`/`access_roles`)
- Migration steps: remove `access_roles` from requested scopes; request `profile`; read `resource_roles` keyed by resource server identifier
- Link to [OIDC Request Scopes](../guides/oidc-request-scopes.md) and [SSO Integration Guide](../guides/sso-integration-guide.md)

- [ ] **Step 2: Add nav entry**

In `docs/.nav.yml`, under Authorization, after Privileged Access Summary (or Super Admin Access), add:

```yaml
    - Roles and Permissions Compatibility: authorization/legacy-roles-permissions-compatibility.md
```

- [ ] **Step 3: Verify page builds**

Run: `source .venv/bin/activate && mkdocs build -q`
Expected: build succeeds; no missing-file warning for the new page.

- [ ] **Step 4: Commit**

```bash
git add docs/authorization/legacy-roles-permissions-compatibility.md docs/.nav.yml
git commit -m "docs: add roles and permissions compatibility appendix (LSA-9677)"
```

---

### Task 2: SSO Integration Guide

**Files:**
- Modify: `docs/guides/sso-integration-guide.md`

**Interfaces:**
- Consumes: appendix link from Task 1
- Produces: correct admin UI narrative for Access Roles / Resource Server Permissions

- [ ] **Step 1: Rewrite TOC and role/permission sections**

Replace TOC entries and sections for:
- App Roles (Client Roles)
- App Permissions
- API Roles (Access Roles)
- API Permissions

With unified sections, for example:
- Access Roles (API Roles)
- Resource Server Permissions
- Client-linked resource servers / client permissions (define term first)
- Client credential roles and permissions

Key content requirements:
- One Access Role model for client-linked and standalone API resource servers
- Permissions are Resource Server Permissions
- UserInfo: request `profile`, example JSON uses `resource_roles` keyed by resource server identifier
- Concise note + link to compatibility appendix for flat `access_roles` / deprecated `/roles`
- Do not teach `scope=openid profile access_roles`

- [ ] **Step 2: Terminology pass on the rest of the file**

Fix remaining App Role / Client Role / App Permission split-model claims without unrelated formatting churn.

- [ ] **Step 3: Commit**

```bash
git add docs/guides/sso-integration-guide.md
git commit -m "docs: unify SSO guide on Access Roles model (LSA-9677)"
```

---

### Task 3: Users and Groups Admin Guide

**Files:**
- Modify: `docs/guides/users-groups-admin-guide.md`

- [ ] **Step 1: Remove Client Roles UI**

- Delete Client Roles Tab section and TOC entry
- Replace group table columns "Client Roles" / "API Roles" with Access Roles (note API Roles synonym if UI still shows it)
- Merge API Roles Tab into Access Roles Tab documentation

- [ ] **Step 2: Align assignment language**

Describe assigning Access Roles (resource-server-scoped) to groups; link appendix for deprecated `/groups/{id}/roles` if needed.

- [ ] **Step 3: Commit**

```bash
git add docs/guides/users-groups-admin-guide.md
git commit -m "docs: remove Client Roles UI from users/groups guide (LSA-9677)"
```

---

### Task 4: Security guides

**Files:**
- Modify: `docs/security/recommended-secure-configuration.md`
- Modify: `docs/security/security-admin-guide.md`

- [ ] **Step 1: Update RBAC section in recommended-secure-configuration.md**

Replace "Client Roles" / "API Roles" split with:
- Access Roles scoped by resource server context (client-linked or standalone)
- Resource Server Permissions as the permission model
- Access-token enforcement via `scope`
- Role checks only from trusted UserInfo/session `resource_roles`

- [ ] **Step 2: Update UMRS in security-admin-guide.md**

Describe UMRS grants as resource-server-scoped Access Roles (not separate client/API role types).

- [ ] **Step 3: Commit**

```bash
git add docs/security/recommended-secure-configuration.md docs/security/security-admin-guide.md
git commit -m "docs: align security guides with unified Access Roles (LSA-9677)"
```

---

### Task 5: Authorization docs consistency

**Files:**
- Modify: `docs/authorization/super-admin-access.md`
- Modify: `docs/authorization/privileged-access-summary.md`
- Modify: `docs/authorization/authorization.md`

- [ ] **Step 1: super-admin-access.md**

Prefer canonical:
- `POST/PUT/DELETE .../access-roles`
- `.../resource-servers/{resourceServerId}/permissions`

Move or replace deprecated client `/roles` and tenant `/permissions` listings with a short notice linking the appendix.

- [ ] **Step 2: privileged-access-summary.md**

Replace "Roles (Client Roles)" / separate Client Roles section with Access Roles + Resource Server Permissions language from monorepo privileged summary.

- [ ] **Step 3: authorization.md**

Align overview with unified model; link appendix for legacy behavior.

- [ ] **Step 4: Commit**

```bash
git add docs/authorization/super-admin-access.md docs/authorization/privileged-access-summary.md docs/authorization/authorization.md
git commit -m "docs: prefer canonical Access Role APIs in authorization docs (LSA-9677)"
```

---

### Task 6: Changeset, audit, build, PR

**Files:**
- Create: `.changeset/<slug>.md`
- Do not modify historical `CHANGELOG.md` entries (changeset release updates it later)

- [ ] **Step 1: Add minor changeset**

```md
---
"authifi-docs": minor
---

Document the unified Access Roles and Resource Server Permissions model, and add a roles/permissions compatibility appendix (LSA-9677).
```

- [ ] **Step 2: Full terminology audit**

Search locally owned sources (exclude synced files and historical CHANGELOG lines):

```bash
rg -n -i 'App Role|Application Role|Client Role|API Role|Access Role|App Permission|Client Permission|API Permission|Access Permission|access_roles|resource_roles|enableLegacyRoles|Client Roles' docs --glob '!feature-list.html' --glob '!guides/oidc-request-scopes.md' --glob '!superpowers/**'
```

Confirm remaining hits are intentional (synonym, appendix, valid generic language).

- [ ] **Step 3: Confirm synced files untouched**

```bash
git diff origin/main -- docs/feature-list.html docs/guides/oidc-request-scopes.md docs/.authifi-sync.json
```

Expected: empty.

- [ ] **Step 4: Build**

```bash
source .venv/bin/activate && mkdocs build
```

Expected: success.

- [ ] **Step 5: Commit changeset + any audit fixes; push; open PR**

PR body must include:
- Summary bullets
- Links to LSA-9677 and auth-monorepo #11589
- Note that synced files were intentionally skipped
- Test plan checklist

- [ ] **Step 6: Babysit**

Per babysit skill: triage unresolved comments, fix in-scope CI failures, report merge-ready or product blocker.

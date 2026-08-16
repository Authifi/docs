## Admin roles and privileged access (Authifi service)

This document describes the Authifi service's admin privilege model and its sources:

- **Super Administrators** (platform-wide)
- **Tenant Administrators** (tenant-wide)
- **Delegated Admins** (users granted elevated `admin::*` permissions)

It complements the detailed operation inventory in [Super Admin Access](super-admin-access.md) and the reference in [Privileged Access Summary](privileged-access-summary.md).

---

### Terminology Note

In user-facing documentation, the platform-wide admin role is called **"Super Administrator"** to distinguish it from infrastructure system administrators who manage servers and operating systems.

---

### Table of contents

- [Super Administrators](#super-administrators)
- [Tenant Administrators](#tenant-administrators)
- [Delegated Admins (admin scopes)](#delegated-admins-admin-scopes)
- [How these roles are enforced](#how-these-roles-are-enforced)
- [Common patterns and examples](#common-patterns-and-examples)
- [Securing admin accounts (best practices)](#securing-admin-accounts-best-practices)

---

## Super Administrators

**What it means**

- Super Administrators have platform-wide privileged access across all tenants.

**How it is determined**

- Super Administrator status is determined by membership in the designated Super Administrator group (configured via `auth.defaults.systemAdminGroup`).
- When a user authenticates, the system checks their group membership and sets an internal authorization flag accordingly.

**What it enables**

- Super Administrators can perform platform-wide privileged operations and are often treated as a bypass for limits and checks.
- Some actions are **still blocked even for Super Administrators** (e.g., certain protections around config-defined Super Administrators).
- See [Super Admin Access](super-admin-access.md) for the full list of SA-only/SA-or-scope operations.

---

## Tenant Administrators

**What it means**

- Tenant Administrators are treated as "full admin within a tenant" for most tenant-scoped APIs.

**How it is determined**

- Tenant Administrator status is determined by membership in the tenant's admin group (configured via `auth.defaults.adminGroup`).

**What it enables**

- **Within a tenant**, Tenant Administrators are generally able to perform tenant admin actions, except where an operation is explicitly restricted to Super Administrators or to an elevated admin scope.
- Tenant Administrators are also used in **cross-tenant authorization** via "trusted tenants" delegation (see below).

---

## Delegated Admins (admin scopes)

**What it means**

- "Delegated Admins" are users who are not Super Administrators, but have elevated permissions via `admin::*` scopes.

**How it is determined**

- Scopes come from the user's permissions/roles and are evaluated at request time.
- These scopes are assigned through role-based access control (RBAC) configuration.

> **Naming Convention:** By convention, delegated admin permissions use the `admin::` prefix (e.g., `admin::mfa:reset`). However, this naming convention alone does not make an entity privileged; the `isPrivileged` flag must be set on the permission, role, or group. See [Privileged Access Summary](privileged-access-summary.md#privileged-entities) for details.

**What it enables**

- These scopes commonly grant access to operations that would otherwise be **Super-Administrator-only** (or allow edits on "privileged" entities).
- Examples of elevated admin scopes used in Authifi:
    - `admin::admin-permissions:edit` (privileged RBAC entities: admin groups/roles/permissions)
    - `admin::system-templates:edit` (system templates)
    - `admin::trusted-provider:edit` (trusted/verified IdP restrictions, some IdP type restrictions)
    - `admin::provider-scripts:edit` (IdP claims mapping scripting)
    - `admin::access-scripts:edit` / `admin::clients:edit` (client scripting / client updates)
    - `admin::view:idp-secrets` (unmask IdP secrets in responses)
    - `admin::user-ssh-secret:edit` (SSH key operations)

For the exact places these are enforced, see the relevant sections in [Super Admin Access](super-admin-access.md).

---

## How these roles are enforced

### 1) API-level authorization checks

- Many API endpoints explicitly check Super Administrator status, Tenant Administrator status, and/or specific `admin::*` permissions.
- Example patterns:
    - **SA-only**: Request is rejected unless the user is a Super Administrator
    - **SA-or-scope**: Super Administrator is allowed, otherwise requires an `admin::*` scope
    - **SA-or-tenant-admin**: Super Administrator is allowed, otherwise allows Tenant Administrators

### 2) Admin override for scope failures

- A large portion of `/auth/admin/tenants/{tenantId}/...` endpoints are primarily protected by **OAuth scopes**.
- When authentication fails due to `Insufficient scope`, the authorization system can allow certain admin users to proceed:
    - Tenant Administrator of the **target tenant** (`{tenantId}`), or
    - Tenant Administrator of the **original token tenant** _and_ the target tenant is a **trusted tenant**.
- This bypass is not applied to **Client Credentials** tokens.

This is why many "admin" APIs don't contain an explicit "Tenant Administrator required" check: Tenant Administrators may still be able to use them even when scopes are missing.

### 3) Trusted tenants delegation

- The service supports delegating admin access across tenants using "trusted tenants" relationships.
- See [Trusted Tenant Implementation](trusted-tenant-implementation.md) for details.

---

## Common patterns and examples

- **Tenant Administrators can do "anything in the tenant", except what is SA-only or scope-gated**
    - Practically, Tenant Administrators can perform most tenant administration, but some operations remain reserved for:
        - **Super Administrators** (SA-only), or
        - **Delegated Admins** (SA-or-scope), especially around privileged RBAC entities and sensitive scripting/secrets.

- **Privileged RBAC entities** (admin groups/roles/permissions)
    - Commonly enforced as **SA-or-scope** (`admin::admin-permissions:edit`).

- **Trusted identity provider controls**
    - Commonly enforced as **SA-or-scope** (`admin::trusted-provider:edit`), with additional Tenant-Administrator-specific logic for some IdP fields.

- **Monitoring / reporting APIs**
    - Often scope-gated (e.g. audit/log scopes, protected health scope), with Tenant Administrator access potentially enabled via the admin override mechanism.

---

## Securing admin accounts (best practices)

- **Prefer "trusted" identity providers for admin identities**
    - Restrict admin sign-in to identity providers that are verified/trusted and centrally managed.
    - Avoid granting admin access to accounts authenticated via ad-hoc or weakly verified providers.

- **Enforce MFA for all admin-capable accounts**
    - Require phishing-resistant MFA where possible (e.g. WebAuthn/FIDO2), at minimum TOTP.
    - Ensure "break-glass" admin accounts are also MFA-protected (with tightly controlled recovery).

- **Apply least privilege and minimize standing access**
    - Keep the number of **Super Administrators** small.
    - Prefer **delegated admin access** (`admin::*`) over Super Administrator where possible.
    - Remove unused admin scopes; time-bound or approval-gated elevation where feasible.

- **Separate admin and day-to-day user accounts**
    - Use dedicated admin accounts for privileged actions.
    - Do not browse email/drive/chat from privileged accounts; reduce exposure to phishing and token theft.

- **Review admin access regularly**
    - Periodically review:
        - Super Administrator assignments,
        - Tenant Administrator group membership,
        - users holding `admin::*` privileges,
        - "trusted tenant" delegations.
    - Treat emergency additions as temporary and require follow-up removal/review.

- **Monitor and audit admin activity**
    - Audit logs should be routinely reviewed for privileged actions (RBAC changes, secrets access, IdP changes, template/script changes, tenant exports).
    - Alert on anomalous admin behavior (new admin grants, repeated denied attempts, unusual login patterns, cross-tenant access via delegation).

- **Harden sessions and tokens**
    - Shorter session lifetimes for admin contexts; revoke sessions promptly on role removal.
    - Rotate credentials/secrets used by admin automation; avoid long-lived tokens.

- **Have an incident-ready process**
    - Document how to revoke admin access quickly (including removing scopes, disabling accounts, revoking sessions).
    - Validate restore/recovery paths for audit logs and configuration (and restrict restore operations appropriately).

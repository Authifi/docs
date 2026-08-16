# Privileged Permissions, Roles, and Groups

This document summarizes privileged access in Authifi. For details, see the linked documentation.

## Table of Contents

- [Overview](#overview)
- [Admin Scopes](#admin-scopes)
- [Privileged Entities](#privileged-entities)
- [Group Membership Roles](#group-membership-roles)
- [Related Documentation](#related-documentation)

---

## Overview

Authifi implements a multi-layered authorization model:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Super Administrator                         │
│              (Platform-wide privileged access)                  │
│    ★ Only Super Admins can manage privileged entities ★         │
├─────────────────────────────────────────────────────────────────┤
│                      Tenant Administrator                       │
│               (Full admin within a single tenant)               │
├─────────────────────────────────────────────────────────────────┤
│                       Delegated Admins                          │
│          (Elevated permissions via admin::* scopes)             │
├─────────────────────────────────────────────────────────────────┤
│                        Regular Users                            │
│    (Access controlled by groups, roles, and permissions)        │
└─────────────────────────────────────────────────────────────────┘
```

### Delegated Admins

Delegated Admins are users who have been granted specific `admin::*` permissions, allowing them to perform targeted administrative tasks without full Super Administrator or Tenant Administrator access. Organizations can delegate tasks such as resetting MFA, editing identity provider settings, and managing templates to trusted users while limiting who can grant the permissions.

### Assigning Privileged Permissions

**Only Super Administrators can create, edit, or assign privileged groups, roles, and permissions.** This restriction prevents privilege escalation by ensuring that:

- Users cannot grant themselves elevated access
- Tenant Administrators cannot create roles that exceed their own authority
- Delegated Admins cannot expand their delegated permissions

Privileged entities are identified by the `isPrivileged` flag. See [Privileged Entities](#privileged-entities) for details.

---

## Admin Scopes

Admin scopes provide granular elevated permissions for Delegated Admins. Users with these scopes can perform specific privileged operations without being a Super Administrator.

> **Important:** Only Super Administrators can assign admin scopes to users. The permissions and roles containing these scopes are themselves privileged entities.

| Scope                    | Value                           | Description                                          |
| ------------------------ | ------------------------------- | ---------------------------------------------------- |
| Reset User MFA           | `admin::mfa:reset`              | Reset multi-factor authentication for users          |
| Update Access Scripts    | `admin::access-scripts:edit`    | Modify client authorization scripts                  |
| IdP Claims Scripting     | `admin::provider-scripts:edit`  | Modify identity provider claims mapping scripts      |
| View IdP Secrets         | `admin::view:idp-secrets`       | View unmasked identity provider secrets              |
| Global Credential Update | `admin::global-secrets:edit`    | Modify global/system-wide secrets                    |
| Send Email               | `admin::sendmail:use`           | Send emails via the Auth API                         |
| Admin Permissions Update | `admin::admin-permissions:edit` | Modify non-privileged RBAC entities (see note below) |
| User SSH Secret          | `admin::user-ssh-secret:edit`   | Manage user SSH key operations                       |
| Trusted Provider Edit    | `admin::trusted-provider:edit`  | Modify trusted/verified identity providers           |
| Update Jobs              | `admin::jobs:edit`              | Create/modify scheduled jobs                         |
| Update System Templates  | `admin::system-templates:edit`  | Modify system HTML/email templates                   |

> **Note on `admin::admin-permissions:edit`:** This scope allows modification of RBAC entities but does **not** grant the ability to create, edit, or assign **privileged** groups, roles, or permissions. Only Super Administrators can manage privileged entities.

### Scope Enforcement Patterns

- **SA-only**: Only Super Administrators can perform the action
- **SA-or-scope**: Super Administrators OR users with the specific scope can proceed
- **SA-or-tenant-admin**: Super Administrators OR Tenant Administrators can proceed
- **SA-bypass**: Super Administrators are exempt from limits/constraints

---

## Privileged Entities

Certain entities are marked as "privileged" and require **Super Administrator** access to manage.

> **Critical Security Control:** Only Super Administrators can create, edit, delete, or assign privileged groups, roles, and permissions. This restriction ensures that elevated access cannot be granted without platform-level oversight.

### What Makes an Entity Privileged?

An entity is privileged when it has `isPrivileged: true` set. This flag is the authoritative indicator that determines whether Super Administrator access is required to manage the entity.

> **Naming Convention:** By convention, privileged permissions use the `admin::` prefix (e.g., `admin::mfa:reset`). However, this naming convention alone does not make an entity privileged; the `isPrivileged` flag must be set.

### Privileged Entity Types

| Entity Type                 | Indicator            | Examples                                 |
| --------------------------- | -------------------- | ---------------------------------------- |
| Groups                      | `isPrivileged: true` | `systemAdmins`, `admins`                 |
| Roles (Client Roles)        | `isPrivileged: true` | `Auth System Admin`, `Auth Tenant Admin` |
| Access Roles                | `isPrivileged: true` | Roles containing admin scopes            |
| Permissions                 | `isPrivileged: true` | Permissions granting elevated access     |
| Resource Server Permissions | `isPrivileged: true` | API-level admin permissions              |

### Required Access for Privileged Entities

| Operation                         | Required Access              |
| --------------------------------- | ---------------------------- |
| Create privileged entity          | **Super Administrator only** |
| Edit privileged entity            | **Super Administrator only** |
| Delete privileged entity          | **Super Administrator only** |
| Assign users to privileged groups | **Super Administrator only** |
| Assign privileged roles to groups | **Super Administrator only** |

> **Why this matters:** Privileged entities grant elevated permissions. If non-Super-Admins could create or assign them, they could escalate their own privileges or grant unauthorized access to others.

---

## Group Membership Roles

Users within groups can have different membership roles that grant varying levels of control.

| Role    | Value     | Capabilities                              |
| ------- | --------- | ----------------------------------------- |
| Member  | `Member`  | Basic group membership                    |
| Manager | `Manager` | Add/remove members, manage group settings |
| Owner   | `Owner`   | Full control over the group               |

### Additional Group Features

- **Expiration dates**: Group membership can have expiry dates
- **Warning dates**: Configurable warning before membership expires
- **Extension requests**: Groups can allow members to request membership extension

---

## Role Types

Authifi supports multiple role types for different use cases:

### Client Roles

- Belong to a specific OAuth Client and Tenant
- Contain Permissions (fine-grained access control)
- Assigned to Groups

### Access Roles

- Belong to a Resource Server (API) and Tenant
- Contain Resource Server Permissions
- Assigned to Groups

---

## Authorization Flow

```
User Request
     │
     ▼
┌────────────────────┐
│ Is Super Admin?    │──Yes──▶ Allow (bypass most checks)
└────────────────────┘         ★ Can manage privileged entities
     │ No
     ▼
┌────────────────────┐
│ Is Tenant Admin?   │──Yes──▶ Allow for tenant operations
└────────────────────┘         (except SA-only/scope-gated)
     │ No                      ✗ Cannot manage privileged entities
     ▼
┌────────────────────┐
│ Is Delegated Admin │──Yes──▶ Allow specific operation
│ (has admin::*)?    │         (based on assigned scope)
└────────────────────┘         ✗ Cannot manage privileged entities
     │ No
     ▼
┌────────────────────┐
│ Standard RBAC      │──Pass──▶ Allow
│ (Groups/Roles/     │
│  Permissions)      │──Fail──▶ Deny
└────────────────────┘
```

---

## Related Documentation

- [Admin Roles and Privileged Access](./admin-roles.md) - Detailed explanation of admin role enforcement
- [Super Administrator Access Requirements](./super-admin-access.md) - Complete inventory of SA-only operations
- [OAuth Client Authorization](./authorization.md) - User groups, AD groups, and RBAC
- [Default Application User Groups](./default-application-user-groups.md) - Auto-assigning users to groups

---

## Quick Reference

### Common Tasks and Required Access

| Task                                  | Required Access                                                    |
| ------------------------------------- | ------------------------------------------------------------------ |
| Create a tenant                       | Super Admin (when restricted)                                      |
| Manage tenant users                   | Tenant Admin                                                       |
| **Create privileged groups**          | **Super Admin only**                                               |
| **Edit privileged roles**             | **Super Admin only**                                               |
| **Assign privileged permissions**     | **Super Admin only**                                               |
| **Add users to `systemAdmins` group** | **Super Admin only**                                               |
| Create non-privileged groups/roles    | Tenant Admin or Delegated Admin                                    |
| Modify trusted identity providers     | Super Admin OR Delegated Admin with `admin::trusted-provider:edit` |
| View IdP secrets (unmasked)           | Super Admin OR Delegated Admin with `admin::view:idp-secrets`      |
| Create system templates               | Super Admin OR Delegated Admin with `admin::system-templates:edit` |
| Manage licenses                       | Super Admin only                                                   |
| Manage issuers                        | Super Admin only                                                   |
| Restore audit logs                    | Super Admin only                                                   |
| Create/modify jobs                    | Super Admin only                                                   |

### Config Keys

| Key                                    | Purpose                                  |
| -------------------------------------- | ---------------------------------------- |
| `auth.defaults.adminGroup`             | Tenant admin group name                  |
| `auth.defaults.systemAdminGroup`       | Super admin group name                   |
| `auth.system.administrators`           | Config-defined super admins (email list) |
| `auth.defaults.restrictTenantCreation` | Restrict tenant creation to super admins |

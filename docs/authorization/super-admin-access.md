## Super Administrator access requirements (Authifi service)

This document lists places in the Authifi service where **Super Administrator** privileges are required **or treated as a privileged bypass**.

### How Super Administrator status is determined

Super Administrator privileges are determined by membership in the designated Super Administrator group (configured via `auth.defaults.systemAdminGroup`). When a user authenticates, the system checks their group membership and sets an internal authorization flag accordingly.

Throughout this document, "system admin" in legacy contexts refers to what is now called "Super Administrator" in user-facing documentation to avoid confusion with infrastructure system administrators.

For a consolidated overview of privileged permissions, roles, and groups, see [Privileged Access Summary](privileged-access-summary.md).

### Legend

- **SA-only**: request is rejected unless the user has Super Administrator privileges.
- **SA-or-scope**: Super Administrator is allowed, but non-super-admins can proceed with a specific elevated scope.
- **SA-or-tenant-admin**: Super Administrator is allowed, but tenant admins can also proceed.
- **Conditional-SA-only**: becomes SA-only only under a config/feature-flag condition.
- **SA-bypass**: Super Administrators are exempt from a limit/constraint that applies to non-super-admins.

### Table of contents

- [Tenants](#tenants)
- [Users & groups](#users-groups)
- [Apps / clients](#apps-clients)
- [Identity providers](#identity-providers)
- [Roles & permissions (privileged RBAC entities)](#roles-permissions-privileged-rbac-entities)
- [Access requests](#access-requests)
- [Secrets](#secrets)
- [Templates (system templates)](#templates-system-templates)
- [Platform / system APIs](#platform-system-apis)

---

## Tenants

- **Create tenant**: `POST /auth/admin/tenants`
    - **UI**: Admins > Tenants > + ADD TENANT
    - **Conditional-SA-only** when `auth.defaults.restrictTenantCreation === true`

- **Create tenant with shared identity providers**: `POST /auth/admin/tenants/createWithProviders`
    - **UI**: Admins > Tenants > + ADD TENANT (with shared IdP options)
    - **Conditional-SA-only** when `auth.defaults.restrictTenantCreation === true`

- **Export tenant data**:
    - `GET /auth/admin/tenants/{tenantId}/export`
    - `GET /auth/admin/tenants/{tenantId}/export/stream`
    - **UI**: Tenant > Settings > Export
    - **SA-or-tenant-admin** (requires admin privileges as determined by the tenant authorization check)

- **Update a tenant certificate name**
    - `PATCH /auth/admin/tenants/{tenantId}/certificates/{id}`
    - **UI**: Tenant > Settings > Edit > Certificates tab
    - **SA-or-tenant-admin**

- **Create tenant beyond license `maxTenants`**
    - **UI**: Admins > Tenants > + ADD TENANT (limit bypass is automatic)
    - **SA-bypass** (Super Administrators bypass the max-tenant limit)

---

## Users & groups

- **Modify a tenant user's `licenseId`**: `PATCH /auth/admin/tenants/{tenantId}/users/{id}`
    - **UI**: API only
    - **SA-only**

- **Add users to the system-admin group**
    - `PUT /auth/admin/tenants/{tenantId}/groups/{groupId}/users/rel/{userId}`
    - `PUT /auth/admin/tenants/{tenantId}/groups/{groupId}/users/assign/bulk`
    - **UI**: Users and Groups > Groups > systemAdmins > Edit > Members tab
    - **SA-only** when the target group is the configured Super Administrator group (`auth.defaults.systemAdminGroup`)

- **Remove config-defined system admins from the system-admin group**
    - `DELETE /auth/admin/tenants/{tenantId}/groups/{groupId}/users/rel/{userId}`
    - **UI**: Users and Groups > Groups > systemAdmins > Edit > Members tab
    - **Not allowed** for users listed in `auth.system.administrators` (even by Super Administrators)

- **Create/update/delete privileged groups** (groups marked `isPrivileged`, including names under the `admin::` namespace)
    - **UI**: Users and Groups > Groups > + ADD GROUP / Edit / Delete
    - **SA-or-scope** (`admin::admin-permissions:edit`)

- **Invite users to a privileged group via data-change requests**
    - `POST /auth/admin/tenants/{tenantId}/data-change-request` (when `groupId` points at a privileged group)
    - **UI**: API only
    - **SA-or-scope** (`admin::admin-permissions:edit`)

- **Tenant user expiration job excludes system admins**
    - **UI**: N/A (automatic system behavior)
    - **SA-bypass** (Super Administrators are not expired by the automated user expiration process)

---

## Apps / clients

- **Create clients beyond license `maxClients`**
    - **UI**: SSO Integration > App Dashboard > + ADD NEW (limit bypass is automatic)
    - **SA-bypass** (Super Administrators bypass the max-client limit)

- **Update SAML access scripts** (claims mapping script, claims authorization script, user-info customization script)
    - `PUT /auth/admin/tenants/{tenantId}/clients/{id}`
    - **UI**: SSO Integration > App Dashboard > Edit app > Scripts tab
    - **SA-or-scope** (non-Super-Administrators must have the relevant elevated scopes, e.g. `admin::access-scripts:edit` and/or `admin::clients:edit`)

---

## Identity providers

- **Set or change `aal_override`**
    - Applies to:
        - `POST /auth/admin/tenants/{tenantId}/identity-providers`
        - `PATCH /auth/admin/tenants/{tenantId}/identity-providers/{id}`
        - `PUT /auth/admin/tenants/{tenantId}/identity-providers/{id}`
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP > Advanced tab
    - **SA-only**

- **Create a trusted identity provider (`isTrusted: true`)**
    - `POST /auth/admin/tenants/{tenantId}/identity-providers`
    - **UI**: SSO Integration > Identity Provider Dashboard > + ADD NEW > check "Trusted"
    - **SA-or-scope** (`admin::trusted-provider:edit`)

- **Change verification status (`isTrusted`) on an existing identity provider**
    - `PATCH/PUT /auth/admin/tenants/{tenantId}/identity-providers/{id}` (when `isTrusted` changes)
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP > toggle "Trusted"
    - **SA-or-scope** (`admin::trusted-provider:edit`)

- **Edit a verified/trusted identity provider**
    - `PATCH/PUT /auth/admin/tenants/{tenantId}/identity-providers/{id}` when the existing provider is already trusted
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP
    - **SA-or-scope** (`admin::trusted-provider:edit`)

- **Set or change IdP `mfaType`**
    - Applies to create and update/replace endpoints.
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP > MFA settings
    - **SA-or-scope** (`admin::trusted-provider:edit`), with additional tenant-admin logic depending on whether the IdP is trusted.

- **Set identity provider claims mapping script** (`config.scripts.mapUserProfile`)
    - `POST/PATCH/PUT /auth/admin/tenants/{tenantId}/identity-providers/{id}`
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP > Scripts tab
    - **SA-or-scope** (`admin::provider-scripts:edit`) in the non-Super-Administrator path

- **Configure secondary unique attributes** (`config.secondaryUniqueAttributes`)
    - `POST/PATCH/PUT /auth/admin/tenants/{tenantId}/identity-providers/{id}`
    - **UI**: SSO Integration > Identity Provider Dashboard > Edit IdP > Advanced tab
    - **SA-or-scope** (`admin::trusted-provider:edit` is treated as a privileged bypass; otherwise blocked)

- **Manage "generic OAuth2" identity providers** (type `OAUTH2`)
    - **UI**: SSO Integration > Identity Provider Dashboard > + ADD NEW > OAuth2 type
    - **SA-or-scope** (`admin::trusted-provider:edit` is treated as a privileged bypass; otherwise `admin::provider-scripts:edit` is required)

- **Identity provider secret fields in API responses** (unmasked)
    - `GET /auth/admin/tenants/{tenantId}/identity-providers/{id}`
    - **UI**: SSO Integration > Identity Provider Dashboard > View IdP (secrets shown if authorized)
    - **SA-or-scope** (`admin::view:idp-secrets`) — secrets are masked in responses unless the caller has this scope or is a Super Administrator

---

## Roles & permissions (privileged RBAC entities)

Use the canonical Access Role and Resource Server Permission APIs for new integrations. Deprecated `/roles` and `/permissions` wrappers are documented in [Roles and Permissions Compatibility](./legacy-roles-permissions-compatibility.md).

- **Create/update/delete privileged Access Roles**
    - `POST /auth/admin/tenants/{tenantId}/access-roles`
    - `PUT /auth/admin/tenants/{tenantId}/access-roles/{id}`
    - `DELETE /auth/admin/tenants/{tenantId}/access-roles/{id}`
    - **UI**: Users and Groups > Roles > + ADD ROLE / Edit / Delete
    - **SA-or-scope** (`admin::admin-permissions:edit`)
    - Extra restriction: default tenant-admin role (`DEFAULT_ROLE.TENANT_ADMIN`) has additional protections against rename/removal.

- **Create/update/delete privileged Resource Server Permissions**
    - `POST /auth/admin/tenants/{tenantId}/resource-servers/{resourceServerId}/permissions`
    - `PATCH /auth/admin/tenants/{tenantId}/resource-servers/{resourceServerId}/permissions/{id}`
    - `DELETE /auth/admin/tenants/{tenantId}/resource-servers/{resourceServerId}/permissions/{id}`
    - **UI**: Users and Groups > Permissions > + ADD PERMISSION / Edit / Delete
    - **SA-or-scope** (`admin::admin-permissions:edit`)

---

## Access requests

- **Approve an access request even if not in the approval chain** (Super Administrator override)
    - `POST /auth/admin/tenants/{tenantId}/access-requests/{id}/approve`
    - **UI**: Access Requests > View Access Requests > Approve button
    - **SA-bypass** (Super Administrators can approve even if not otherwise an authorized approver)

- **Update a TOTP Reset request template**
    - `PUT /auth/admin/tenants/{tenantId}/requestable-accesses/{id}` (when `accessType === TOTP_RESET`)
    - **UI**: Access Requests > Access Request Templates > Edit TOTP Reset template
    - **SA-or-tenant-admin**

- **Delete the default-tenant TOTP Reset request template**
    - `DELETE /auth/admin/tenants/{tenantId}/requestable-accesses/{id}`
    - **UI**: Access Requests > Access Request Templates > Delete TOTP Reset template
    - **Conditional-SA-only** when deleting a `TOTP_RESET` template in the **default tenant**

---

## Secrets

- **Read plaintext for a system-shared tenant secret**
    - `GET /auth/admin/tenants/{tenantId}/secrets/{secretId}` (when the secret is shared at `SharedLevel.System`)
    - **UI**: Resources and Tools > Secrets > View secret
    - **SA-only**
    - Note: secrets marked `SecretWriteOnly` cannot be read.

- **Backfill users' TOTP secrets to JWE**
    - `POST /auth/admin/secrets/backfill-totp-secrets-jwe`
    - **UI**: API only
    - **SA-only**

---

## Templates (system templates)

- **Revert an HTML template to system default**
    - `POST /auth/admin/tenants/{tenantId}/html-templates/{id}/revert-to-system-default`
    - **UI**: Resources and Tools > HTML Templates > Revert to Default
    - **SA-only**

- **Create or update a system HTML template** (when `isSystemTemplate === true`)
    - `POST /auth/admin/tenants/{tenantId}/html-template`
    - `PUT /auth/admin/tenants/{tenantId}/html-template/{id}`
    - **UI**: Resources and Tools > HTML Templates > + ADD / Edit (with system template flag)
    - **SA-or-scope** (`admin::system-templates:edit`)

- **Create or update a system email template** (when `isSystemTemplate === true`)
    - `POST /auth/admin/tenants/{tenantId}/email-template`
    - `PUT /auth/admin/tenants/{tenantId}/email-template/{id}`
    - **UI**: Resources and Tools > Email Templates > + ADD / Edit (with system template flag)
    - **SA-or-scope** (`admin::system-templates:edit`)

---

## Platform / system APIs

### Licensing

- **Create a license**: `POST /auth/admin/licenses`
    - **UI**: Admins > Licenses > + ADD LICENSE
    - **SA-only**

- **Update a license**: `PATCH /auth/admin/licenses/{licenseId}`
    - **UI**: Admins > Licenses > Edit license
    - **SA-only**

- **Delete a license**: `DELETE /auth/admin/licenses/{licenseId}`
    - **UI**: Admins > Licenses > Delete license
    - **SA-only**

- **Assign a license to a user**: `PUT /auth/admin/licenses/{licenseId}/rel/{userId}`
    - **UI**: API only (license assignment to tenants via Tenant > Settings > Edit > Metadata tab)
    - **SA-only**

### Issuers

- **Create/update/delete issuer**
    - `POST /auth/admin/issuers`
    - `PATCH /auth/admin/issuers`
    - `PATCH /auth/admin/issuers/{id}`
    - `PUT /auth/admin/issuers/{id}`
    - `DELETE /auth/admin/issuers/{id}`
    - **UI**: API only
    - **SA-only**

### Allowed origins (global CORS allow-list)

- **List global allowed origins**: `GET /auth/admin/allowed-origins/global`
    - **UI**: API only
- **Refresh allowed-origins cache**: `POST /auth/admin/allowed-origins/refresh-cache`
    - **UI**: API only
- **Get allow-list enabled state**: `GET /auth/admin/allowed-origins/allowlist-enabled`
    - **UI**: API only
- **Set allow-list enabled state**: `PATCH /auth/admin/allowed-origins/allowlist-enabled`
    - **UI**: API only
- **Get discovery mode state**: `GET /auth/admin/allowed-origins/discovery-mode`
    - **UI**: API only
- **Set discovery mode state**: `PATCH /auth/admin/allowed-origins/discovery-mode`
    - **UI**: API only
    - **SA-only**

- **Create a global allowed origin**: `POST /auth/admin/tenants/{tenantId}/allowed-origins` with body `{ isGlobal: true }`
    - **UI**: API only
    - **SA-only**

- **List global origins via tenant endpoint**: `GET /auth/admin/tenants/{tenantId}/allowed-origins` with `filter.where.isGlobal === true`
    - **UI**: API only
    - **SA-only**

- **Toggle global status on an origin**: `PATCH /auth/admin/tenants/{tenantId}/allowed-origins/{id}` when request body includes `isGlobal`
    - **UI**: API only
    - **SA-only**

### System notifications

- **Set a notification as global**: `POST /auth/admin/tenants/{tenantId}/system-notification` with `isGlobal: true`
    - **UI**: Resources and Tools > System Notifications > + ADD (with global flag)
    - **SA-only**

- **Change global notification settings**: `PATCH /auth/admin/tenants/{tenantId}/system-notification/{id}` when request changes `isGlobal`
    - **UI**: Resources and Tools > System Notifications > Edit (toggle global)
    - **SA-only**

### Audit

- **Restore audit logs**: `POST /auth/admin/tenants/{tenantId}/audit/restore`
    - **UI**: API only
    - **SA-only**

- **Audit chain verification / last hash**
    - `GET /auth/admin/audit-chain/verify`
    - `GET /auth/admin/audit-chain/verify-checkpoints`
    - `GET /auth/admin/audit-chain/verify-last-week`
    - `GET /auth/admin/audit-chain/verify-last-month`
    - `GET /auth/admin/audit-chain/last-hash`
    - **UI**: API only
    - **SA-or-client-credentials** (allowed if Super Administrator OR using client-credentials grant)

### Jobs

- **Create/update/delete a job**
    - `POST /auth/admin/tenants/{tenantId}/job`
    - `PUT /auth/admin/tenants/{tenantId}/job/{id}`
    - `DELETE /auth/admin/tenants/{tenantId}/job/{id}`
    - **UI**: Resources and Tools > Jobs > + ADD / Edit / Delete
    - **SA-only**

### SSH manager

- **Create or update SSH access requests (user SSH keys)**
    - `POST /auth/tenants/{tenantId}/user/ssh/ssh-request`
    - `PUT /auth/tenants/{tenantId}/user/ssh/ssh-request`
    - **UI**: API only
    - **SA-or-scope** (`admin::user-ssh-secret:edit`)

### Uploads

- **Upload files while uploads are disabled**
    - `POST /auth/admin/tenants/{tenantId}/uploads`
    - **UI**: Various upload dialogs (when enabled for Super Administrators)
    - **Conditional-SA-only** when `auth.uploads.enabled === false` and `auth.uploads.systemAdminsOnly === true`

### Landing pages

- **Edit landing pages**
    - `POST /auth/admin/tenants/{tenantId}/landing-pages`
    - `PUT /auth/admin/tenants/{tenantId}/landing-pages/{id}`
    - **UI**: Tenant > Settings > Edit > Landing Page tab
    - **Conditional-SA-only** when `auth.landingPage.systemAdminsEnabled === true` and `auth.landingPage.tenantAdminsEnabled === false`

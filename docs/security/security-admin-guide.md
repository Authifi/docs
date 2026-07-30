# Authifi Service - Security Admin Guide

This document provides comprehensive guidance for securely setting up, configuring, operating, and decommissioning administrative accounts within the Authifi service. It is intended for security administrators, tenant administrators, and delegated admins who manage authentication and authorization functions.

**Compliance**: FedRAMP Rev5 Recommended Secure Configuration

---

## Table of Contents

1. [Document Information](#document-information)
2. [Purpose and Scope](#purpose-and-scope)
3. [Administrative Account Types](#administrative-account-types)
   - [Super Administrators](#super-administrators-top-level-administrative-accounts)
   - [Tenant Administrators](#tenant-administrators-privileged-accounts)
   - [Delegated Admins](#delegated-admins)
4. [Account Lifecycle Management](#account-lifecycle-management)
   - [Super Administrator Lifecycle](#super-administrator-lifecycle)
   - [Tenant Administrator Lifecycle](#tenant-administrator-lifecycle)
   - [Delegated Admin Lifecycle](#delegated-admin-lifecycle)
5. [Security Settings Reference Tables](#security-settings-reference-tables)
   - [Super Administrator Security Settings](#super-administrator-security-settings-reference)
   - [Tenant Administrator Security Settings](#tenant-administrator-security-settings-reference)
   - [Delegated Admin Security Settings](#delegated-admin-security-settings-reference)
6. [Recommended Secure Configuration](#recommended-secure-configuration)
7. [Security Best Practices](#security-best-practices)
8. [Compliance Requirements](#compliance-requirements)

---

## Document Information

| Attribute            | Value                                             |
| -------------------- | ------------------------------------------------- |
| **Document Version** | 1.0                                               |
| **Last Updated**     | 2026-01-22                                        |
| **Applies To**       | Authifi Service                                   |
| **Compliance**       | FedRAMP Rev5 Recommended Secure Configuration     |
| **Classification**   | Public                                            |
| **Accessibility**    | Available without authentication at published URL |

**Related Documentation**:

- [Recommended Secure Configuration](./recommended-secure-configuration.md) - Detailed security configuration guidance
- [Super Admin Access Requirements](../authorization/super-admin-access.md) - Complete list of super-admin-only operations
- [Admin Roles Overview](../authorization/admin-roles.md) - Role hierarchy and enforcement
- [Tenant Administrator Guide](../guides/tenant-admin-guide.md) - Tenant settings configuration

---

## Purpose and Scope

This Security Admin Guide documents all administrative profiles within the Authifi service that are accessible to:

- **Authifi internal users** (platform operators and support personnel)
- **End customers** (tenant administrators and delegated administrators)

The guide covers any user profile with elevated permissions and action privileges that can affect the security posture of other users and the application as a whole.

### Terminology

| Term                     | Definition                                                                                                                                                     |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Super Administrator**  | Application-level administrator with full control over the Authifi platform. Highest privilege level. Can manage all tenants and platform-wide configurations. |
| **Tenant Administrator** | Administrator with full control within a single tenant. Cannot access other tenants or super admin functions.                                               |
| **Delegated Admin**      | User with specific elevated permissions for particular resource types without full admin access.                                                                |

> **Note**: The Authifi service codebase uses identifiers such as `DEFAULT_ROLE.SYSTEM_ADMIN` (role name: "Auth System Admin"), `systemAdmins` group, and `isSystemAdmin` checks. These refer to Super Administrator in user-facing documentation.

> **Note**: Infrastructure Administrators are not covered in this document. Refer to the infrastructure provider's Recommended Secure Configuration Guide.

---

## Administrative Account Types

### Account Hierarchy

```
┌───────────────────────────────────────────────────────────────────┐
│   SUPER ADMINISTRATORS (Top-Level Administrative Accounts)        │
│   ─────────────────────────────────────────────────────────────── │
│   • Platform-wide control across all tenants                      │
│   • License and quota management                                  │
│   • Identity provider trust configuration                         │
│   • System-level security settings                                │
│   • Role name: Auth System Admin                                  │
│   • Group: systemAdmins (in default tenant)                       │
└───────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────┐
│   TENANT ADMINISTRATORS (Privileged Accounts - Tenant Scope)      │
│   ─────────────────────────────────────────────────────────────── │
│   • Full control within assigned tenant                           │
│   • User/group/application management                             │
│   • Tenant-level security configuration                           │
│   • Identity provider setup (except trust status)                 │
│   • Access via: Admin group membership or admin permissions       │
└───────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────┐
│   DELEGATED ADMINS (Scoped Permissions)                           │
│   ─────────────────────────────────────────────────────────────── │
│   • Limited to specific resource types                            │
│   • Delegated administration via UMRS roles                       │
│   • admin::* permissions for targeted capabilities                │
│   • Cannot modify tenant-wide settings                            │
└───────────────────────────────────────────────────────────────────┘
```

---

### Super Administrators (Top-Level Administrative Accounts)

Super Administrators have unrestricted access to the Authifi platform. This is the highest level of administrative privilege that can be assumed by end customers.

#### Role Definition

| Attribute        | Description                                                                         |
| ---------------- | ----------------------------------------------------------------------------------- |
| **Role Name**    | `Auth System Admin`                                                                 |
| **Group**        | `systemAdmins` (configured in `auth.defaults.systemAdminGroup`)                     |
| **Scope**        | Platform-wide (all tenants)                                                         |
| **Grant Method** | Membership in `systemAdmins` group or configuration in `auth.system.administrators` |

#### Permissions and Operations

**Platform Management Operations** (Super Admin Only):

| Operation                       | API Endpoint                                   | UI Location                                | Description                                                 |
| ------------------------------- | ---------------------------------------------- | ------------------------------------------ | ----------------------------------------------------------- |
| Create tenant (when restricted) | `POST /auth/admin/tenants`                     | Admins > Tenants > + ADD TENANT            | Create new tenants when `restrictTenantCreation` is enabled |
| Manage licenses                 | `POST/PATCH/DELETE /auth/admin/licenses`       | Admins > Licenses                          | Create, update, and delete licenses                         |
| Assign licenses to users        | `PUT /auth/admin/licenses/{id}/rel/{userId}`   | API only                                   | Associate users with licenses                               |
| Manage issuers                  | `POST/PATCH/DELETE /auth/admin/issuers`        | API only                                   | Create, update, and delete token issuers                    |
| Create jobs                     | `POST/PUT/DELETE /auth/admin/tenants/{id}/job` | Resources and Tools > Jobs                 | Manage scheduled jobs                                       |
| Restore audit logs              | `POST /auth/admin/tenants/{id}/audit/restore`  | API only                                   | Restore deleted audit records                               |

**Identity Provider Operations** (Super Admin Only):

| Operation           | API Endpoint                                                 | UI Location                                          | Description                                       |
| ------------------- | ------------------------------------------------------------ | ---------------------------------------------------- | ------------------------------------------------- |
| Set AAL override    | Create/Update IdP endpoints                                  | SSO Integration > Identity Provider Dashboard > Edit | Configure Authentication Assurance Level override |
| Create trusted IdP  | `POST /auth/admin/tenants/{id}/identity-providers`           | SSO Integration > Identity Provider Dashboard > + ADD NEW | Create identity provider with `isTrusted: true`   |
| Modify trust status | `PATCH/PUT /auth/admin/tenants/{id}/identity-providers/{id}` | SSO Integration > Identity Provider Dashboard > Edit | Change `isTrusted` flag on existing IdP           |
| Edit trusted IdP    | `PATCH/PUT /auth/admin/tenants/{id}/identity-providers/{id}` | SSO Integration > Identity Provider Dashboard > Edit | Modify already-trusted identity provider          |

**Security Operations** (Super Admin Only):

| Operation                       | API Endpoint                                                  | UI Location                                     | Description                            |
| ------------------------------- | ------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------- |
| Add users to systemAdmins group | `PUT /auth/admin/tenants/{id}/groups/{id}/users/rel/{userId}` | Users and Groups > Groups > systemAdmins > Edit | Grant super admin access               |
| Create global CORS origins      | `POST /auth/admin/tenants/{id}/allowed-origins`               | API only                                        | Create origins applying to all tenants |
| Manage CORS allow-list          | `PATCH /auth/admin/allowed-origins/allowlist-enabled`         | API only                                        | Enable/disable CORS enforcement        |
| Create global notifications     | `POST /auth/admin/tenants/{id}/system-notification`           | Resources and Tools > System Notifications      | Create platform-wide notifications     |
| Revert system templates         | `POST /html-templates/{id}/revert-to-system-default`          | Resources and Tools > HTML Templates > Revert   | Reset templates to defaults            |

**Bypass Capabilities** (Limits Super Admins Bypass):

| Bypass            | Description                                                 |
| ----------------- | ----------------------------------------------------------- |
| Max tenants limit | Can create tenants beyond license `maxTenants`              |
| Max clients limit | Can create applications beyond license `maxClients`         |
| User expiration   | Super admin accounts exempt from automatic expiration       |
| Approval chains   | Can approve access requests without being in approval chain |

---

### Tenant Administrators (Privileged Accounts)

Tenant Administrators have full administrative control within a single tenant. They cannot access other tenants or perform super administrator functions.

#### Role Definition

| Attribute        | Description                                              |
| ---------------- | -------------------------------------------------------- |
| **Grant Method** | Membership in tenant admin group (configured per tenant) |
| **Scope**        | Single tenant                                            |

#### Permissions and Operations

**Tenant Configuration**:

| Operation                | Capability                                            | Security Impact                         |
| ------------------------ | ----------------------------------------------------- | --------------------------------------- |
| Edit tenant settings     | Modify session lifetimes, branding, security settings | High - affects all tenant users         |
| Configure MFA policies   | Enable/disable admin MFA, set TOTP parameters         | High - controls authentication strength |
| Manage allowed origins   | Add/remove CORS origins for tenant                    | High - controls application access      |
| Configure email settings | Set SMTP server, sender address                       | Medium - affects communications         |
| Manage JWKS keys         | Rotate JWT signing keys                               | High - affects token validation         |
| Manage SAML certificates | Upload/rotate certificates                            | High - affects federation trust         |

**User and Access Management**:

| Operation                 | Capability                         | Security Impact                    |
| ------------------------- | ---------------------------------- | ---------------------------------- |
| Create/manage users       | Full user lifecycle within tenant  | High - controls access             |
| Manage groups             | Create groups, assign memberships  | High - affects permissions         |
| Assign roles              | Grant/revoke roles and permissions | High - controls authorization      |
| Configure access requests | Set up approval workflows          | Medium - affects access governance |
| Import/export users       | Bulk user operations               | High - data access implications    |

**Application Management**:

| Operation               | Capability                               | Security Impact                            |
| ----------------------- | ---------------------------------------- | ------------------------------------------ |
| Create applications     | Register OAuth/SAML clients              | High - creates access points               |
| Manage client secrets   | Generate/rotate secrets                  | High - controls application authentication |
| Configure redirect URIs | Set valid redirect destinations          | High - affects OAuth security              |
| Set token lifetimes     | Configure access/refresh token durations | Medium - affects session security          |

**Identity Provider Management**:

| Operation                       | Capability                        | Security Impact               |
| ------------------------------- | --------------------------------- | ----------------------------- |
| Create identity providers       | Add OAuth/OIDC/SAML/LDAP IdPs     | High - affects authentication |
| Configure claims mapping        | Map IdP claims to user attributes | High - affects authorization  |
| Test IdP connections            | Verify IdP configuration          | Low                           |
| **Cannot** set `isTrusted` flag | Requires Super Admin              | -                             |
| **Cannot** set AAL override     | Requires Super Admin              | -                             |

**Monitoring and Audit**:

| Operation          | Capability                       | Security Impact                         |
| ------------------ | -------------------------------- | --------------------------------------- |
| View audit logs    | Read all tenant audit entries    | Low (read-only)                         |
| Export audit logs  | Export logs to JSON/CSV          | Medium - data extraction                |
| View login events  | Monitor authentication activity  | Low (read-only)                         |
| Export tenant data | Full tenant configuration export | High - contains sensitive configuration |

---

### Delegated Admins

Delegated Admins have elevated permissions for specific resource types without full tenant admin access. This enables delegated administration following the principle of least privilege.

#### Admin Scope Permissions

| Scope                            | Capability                                                 | Typical Use Case            |
| -------------------------------- | ---------------------------------------------------------- | --------------------------- |
| `admin::admin-permissions:edit`  | Create/modify privileged groups, roles, permissions        | Security team managing RBAC |
| `admin::system-templates:edit`   | Create/modify system HTML and email templates              | Branding team               |
| `admin::trusted-provider:edit`   | Modify trusted IdPs, configure secondary unique attributes | IdP administrators          |
| `admin::provider-scripts:edit`   | Configure IdP claims mapping scripts                       | Integration specialists     |
| `admin::view:idp-secrets`        | View unmasked IdP secrets in API responses                 | Integration debugging       |
| `admin::access-scripts:edit`     | Modify SAML access scripts                                 | SAML integration team       |
| `admin::clients:edit`            | Update client applications                                 | Application administrators  |
| `admin::user-ssh-secret:edit`    | Create/update SSH access requests                          | DevOps team                 |

> **Naming Convention:** By convention, delegated admin permissions use the `admin::` prefix. However, this naming convention alone does not make an entity privileged—the `isPrivileged` flag must be set on the permission, role, or group.

#### User-Managed Role System (UMRS)

UMRS enables resource-server-scoped delegation where designated managers can grant specific Access Roles.

> **Note**: UMRS grants Access Roles in a resource-server context. Authifi securely manages the role policy; interpretation and enforcement of those roles in the application or API is application-dependent.

| Component               | Description                                              |
| ----------------------- | -------------------------------------------------------- |
| **UMRS Role**           | Access Role defining what access is granted              |
| **Manager Group**       | Who can grant this Access Role                           |
| **Resource Server**     | Authorization context (client-linked or standalone API)  |
| **Time-Limited Grants** | Supports expiration dates                                |
| **Extension Requests**  | Users can request grant extensions                       |

---

## Account Lifecycle Management

### Super Administrator Lifecycle

#### Setup and Provisioning

**Prerequisites**:

- Existing super administrator account to perform assignment
- User account exists in the platform
- User authenticates via a trusted identity provider (`isTrusted: true`)
- MFA enrolled (WebAuthn/FIDO2 strongly recommended, TOTP minimum)

**Provisioning Process**:

| Step | Action                                               | Verification                      |
| ---- | ---------------------------------------------------- | --------------------------------- |
| 1    | Existing super admin accesses system configuration   | Authenticate with MFA             |
| 2    | Add user to `systemAdmins` group                     | Confirm group membership          |
| 3    | System grants `Auth System Admin` role automatically | Verify role assignment            |
| 4    | Security alert sent to all existing super admins     | Confirm alert delivery            |
| 5    | Verify MFA enrollment                                | Check TOTP/WebAuthn registration  |
| 6    | Document business justification                      | Record approval and justification |

**Temporary Access** (for audits, incident response):

| Step | Action                                        | Requirement |
| ---- | --------------------------------------------- | ----------- |
| 1    | Specify expiration date/time                  | Required    |
| 2    | Document temporary access reason              | Required    |
| 3    | Access automatically revoked after expiration | Automatic   |
| 4    | All actions logged with temporary flag        | Automatic   |

**Security Checklist**:

- [ ] User authenticates via trusted identity provider
- [ ] MFA enabled and enrolled (WebAuthn or TOTP)
- [ ] User identity verified through out-of-band channel
- [ ] Business justification documented
- [ ] All existing super admins notified
- [ ] User acknowledged security responsibilities
- [ ] Backup contact information recorded
- [ ] Temporary access expiration set (if applicable)

#### Operation Requirements

**Authentication Requirements**:

| Requirement       | Setting                               | Recommendation        |
| ----------------- | ------------------------------------- | --------------------- |
| Identity Provider | Must be marked `isTrusted: true`      | Required              |
| MFA               | Mandatory for all super admin actions | Required              |
| MFA Type          | WebAuthn/FIDO2 or TOTP                | WebAuthn preferred    |
| Session Lifetime  | Configurable                          | 2-8 hours maximum     |
| Idle Timeout      | Configurable                          | 30-60 minutes maximum |

**Operational Security**:

| Practice                | Description                                           |
| ----------------------- | ----------------------------------------------------- |
| Dedicated admin account | Separate from day-to-day user account                 |
| Secure workstation      | Use company-managed devices only                      |
| Network security        | Use VPN; avoid public WiFi                            |
| Session management      | Log out when done; never save passwords in browser    |
| Alert monitoring        | Review and respond to security alerts within 24 hours |

#### Decommissioning

**Immediate Actions**:

| Step | Action                                         | Automated? |
| ---- | ---------------------------------------------- | ---------- |
| 1    | Remove user from `systemAdmins` group          | Manual     |
| 2    | `Auth System Admin` role automatically revoked | Automatic  |
| 3    | Security alert sent to remaining super admins  | Automatic  |
| 4    | Audit log entry created with reason            | Automatic  |
| 5    | Revoke all active sessions immediately         | Manual     |
| 6    | Revoke all active OAuth grants                 | Manual     |

**Post-Decommission Actions**:

| Action                   | Timeframe                      | Description                                  |
| ------------------------ | ------------------------------ | -------------------------------------------- |
| Audit log review         | Within 48 hours                | Export and review last 90 days of activity   |
| Secret rotation          | Within 24 hours if compromised | Rotate any shared secrets accessed           |
| Account retention        | Indefinite (disabled)          | Maintain for audit trail; do not delete      |
| Compliance documentation | Within 7 days                  | Document removal in change management system |

**Secret Rotation Priority** (if applicable):

- Credentials accessed in last 30 days (highest priority)
- API keys and service account credentials
- Encryption keys (if compromise suspected)
- All secrets accessed during tenure (lower priority)

---

### Tenant Administrator Lifecycle

#### Setup and Provisioning

**Method 1: Group-Based Assignment (Recommended)**

| Step | Action                                | Result                      |
| ---- | ------------------------------------- | --------------------------- |
| 1    | Identify or create tenant admin group | Group exists                |
| 2    | Add user to admin group               | Membership recorded         |
| 3    | User gains tenant admin capabilities  | Automatic                   |
| 4    | Verify MFA enrollment                 | Confirm TOTP/WebAuthn setup |

**Method 2: Direct Permission Assignment**

| Step | Action                                           | Result              |
| ---- | ------------------------------------------------ | ------------------- |
| 1    | Assign tenant-admin-capable permissions to user  | Permissions granted |
| 2    | Specify permission scopes (e.g., `auth.admin.*`) | Scopes applied      |
| 3    | User gains targeted admin capabilities           | Based on scopes     |

**Security Recommendations**:

- Use group-based assignment for easier auditing
- Require MFA enrollment before granting admin access
- Document business justification for each admin
- Set up regular access reviews (quarterly minimum)

#### Operation Requirements

**Authentication Requirements**:

| Requirement       | Setting                               | Recommendation               |
| ----------------- | ------------------------------------- | ---------------------------- |
| Identity Provider | Trusted IdP recommended               | Strongly recommended         |
| MFA               | Per tenant "Enable Admin MFA" setting | Enable in production         |
| Session Lifetime  | Tenant configurable                   | 4-8 hours for admin contexts |
| Idle Timeout      | Tenant configurable                   | 30-60 minutes                |

**Configurable Security Settings** (Tenant Admin Controls):

| Setting                     | Description                       | Recommended Value   |
| --------------------------- | --------------------------------- | ------------------- |
| Enable Admin MFA            | Require MFA for admin actions     | `true`              |
| Always Require MFA When Set | Enforce MFA for enrolled users    | `true`              |
| TOTP Suspension Threshold   | Failed attempts before suspension | 5                   |
| TOTP Lockout Threshold      | Failed attempts before lockout    | 10                  |
| TOTP Suspension Period      | Suspension duration (minutes)     | 2                   |
| Session Lifetime            | Maximum session duration          | 14400-28800 seconds |
| Idle Session Lifetime       | Inactivity timeout                | 1800-3600 seconds   |

#### Decommissioning

| Step | Action                                 | Timing          |
| ---- | -------------------------------------- | --------------- |
| 1    | Remove user from tenant admin group    | Immediate       |
| 2    | User loses tenant admin capabilities   | Immediate       |
| 3    | Revoke active sessions                 | Immediate       |
| 4    | Export and review audit logs (90 days) | Within 48 hours |
| 5    | Document removal                       | Within 7 days   |

---

### Delegated Admin Lifecycle

#### Setup and Provisioning

**Method 1: Direct Scope Assignment**

| Step | Action                                        | Result                     |
| ---- | --------------------------------------------- | -------------------------- |
| 1    | Identify required `admin::*` permissions      | Scopes identified          |
| 2    | Assign permissions to user                    | Permissions granted        |
| 3    | User gains delegated admin capabilities       | Limited to assigned scopes |

**Method 2: UMRS Role Grant**

| Step | Action                              | Result              |
| ---- | ----------------------------------- | ------------------- |
| 1    | Create UMRS Access Role scoped to a resource server | Role exists         |
| 2    | Designate manager group             | Managers identified |
| 3    | Manager grants role to user         | Access granted      |
| 4    | Set expiration date (optional)      | Time-limited access |

#### Decommissioning

| Step | Action                             | Timing          |
| ---- | ---------------------------------- | --------------- |
| 1    | Remove `admin::*` permissions      | Immediate       |
| 2    | Revoke UMRS grants                 | Immediate       |
| 3    | Revoke active sessions             | Immediate       |
| 4    | Audit log review                   | Within 48 hours |

---

## Security Settings Reference Tables

### Super Administrator Security Settings Reference

The following settings are controlled exclusively by Super Administrators or provide Super Administrator bypass capabilities.

#### Platform-Level Settings

| Setting                                | Location      | Function                                                | Security Impact                 | Recommended Value        |
| -------------------------------------- | ------------- | ------------------------------------------------------- | ------------------------------- | ------------------------ |
| `auth.defaults.restrictTenantCreation` | Configuration | When `true`, only super admins can create tenants       | High - Controls multi-tenancy   | `true` for production    |
| `auth.defaults.systemAdminGroup`       | Configuration | Group name for super administrators                     | Critical - Defines admin access | `systemAdmins` (default) |
| `auth.system.administrators`           | Configuration | Config-defined super admins (cannot be removed via API) | Critical - Break-glass access   | Minimal list (1-2)       |
| `auth.uploads.systemAdminsOnly`        | Configuration | When `true`, only super admins can upload files         | Medium                          | Based on requirements    |
| `auth.landingPage.systemAdminsEnabled` | Configuration | Super admins can edit landing pages                     | Low                             | `true`                   |
| `auth.landingPage.tenantAdminsEnabled` | Configuration | Tenant admins can edit landing pages                    | Low                             | Based on requirements    |

#### Identity Provider Settings (Super Admin Only)

| Setting                                 | API Field      | Function                                     | Security Impact                          | Recommended Value                |
| --------------------------------------- | -------------- | -------------------------------------------- | ---------------------------------------- | -------------------------------- |
| Authentication Assurance Level Override | `aal_override` | Override AAL for IdP (AAL1/AAL2/AAL3)        | High - Affects authentication strength   | AAL2 or AAL3 for admin IdPs      |
| Trusted Identity Provider               | `isTrusted`    | Marks IdP as trusted for elevated privileges | Critical - Enables admin access from IdP | Only for enterprise-managed IdPs |
| MFA Type                                | `mfaType`      | Specifies MFA method for IdP                 | High - Informs authorization decisions   | Match actual IdP configuration   |

#### License Management Settings (Super Admin Only)

| Setting            | API Field                 | Function                         | Security Impact              | Recommended Value            |
| ------------------ | ------------------------- | -------------------------------- | ---------------------------- | ---------------------------- |
| Max Tenants        | `maxTenants`              | Maximum tenants per license      | Medium - Resource governance | Based on licensing agreement |
| Max Clients        | `maxClients`              | Maximum applications per license | Medium - Resource governance | Based on licensing agreement |
| License Assignment | User-license relationship | Associates users with licenses   | Medium - Feature access      | Assign based on user role    |

#### Global CORS Settings (Super Admin Only)

| Setting            | API Endpoint                             | Function                             | Security Impact                    | Recommended Value           |
| ------------------ | ---------------------------------------- | ------------------------------------ | ---------------------------------- | --------------------------- |
| Allow-list Enabled | `/allowed-origins/allowlist-enabled`     | Enable/disable CORS enforcement      | Critical - Controls API access     | `true` always in production |
| Discovery Mode     | `/allowed-origins/discovery-mode`        | Log origins for allow-list migration | Medium - Temporary troubleshooting | `false` in production       |
| Global Origins     | `/allowed-origins` with `isGlobal: true` | Create origins for all tenants       | High - Platform-wide access        | Minimize; document each     |

---

### Tenant Administrator Security Settings Reference

The following settings are controlled by Tenant Administrators (and Super Administrators).

#### Authentication Settings

| Setting                     | UI Location  | API Field                 | Function                                         | Security Impact                 | Recommended Value       |
| --------------------------- | ------------ | ------------------------- | ------------------------------------------------ | ------------------------------- | ----------------------- |
| Session Lifetime            | Settings Tab | `sessionLifetime`         | Maximum authenticated session duration (seconds) | High - Exposure window          | 14400-28800 (4-8 hours) |
| Idle Session Lifetime       | Settings Tab | `idleSessionLifetime`     | Inactivity timeout (seconds)                     | High - Unattended session risk  | 1800-3600 (30-60 min)   |
| Refresh Token Lifetime      | Settings Tab | `refreshTokenLifetime`    | Refresh token validity (minutes)                 | Medium - Extended access        | 10080-20160 (7-14 days) |
| Enable Admin MFA            | Settings Tab | `enableAdminMfa`          | Require MFA for admin actions                    | Critical - Admin protection     | `true`                  |
| Always Require MFA When Set | Settings Tab | `requireMfaWhenSet`       | Enforce MFA for enrolled users                   | High - Consistent MFA           | `true`                  |
| TOTP Suspension Threshold   | Settings Tab | `totpSuspensionThreshold` | Failed TOTP attempts before suspension           | Medium - Brute force protection | 5                       |
| TOTP Lockout Threshold      | Settings Tab | `totpLockoutThreshold`    | Failed TOTP attempts before lockout              | Medium - Brute force protection | 10                      |
| TOTP Suspension Period      | Settings Tab | `totpSuspensionPeriod`    | Suspension duration (minutes)                    | Medium - Attack mitigation      | 2                       |

#### CORS and Origin Settings

| Setting         | UI Location         | API Field        | Function                      | Security Impact               | Recommended Value                |
| --------------- | ------------------- | ---------------- | ----------------------------- | ----------------------------- | -------------------------------- |
| Allowed Origins | Allowed Origins Tab | `allowedOrigins` | Valid CORS origins for tenant | Critical - API access control | Only trusted, controlled origins |
| HTTPS Required  | -                   | Validation rule  | Origins must use HTTPS        | High - Transport security     | Enforce HTTPS in production      |

#### Login Security Settings

| Setting                | UI Location    | API Field                        | Function                            | Security Impact        | Recommended Value             |
| ---------------------- | -------------- | -------------------------------- | ----------------------------------- | ---------------------- | ----------------------------- |
| Enable Disclaimer      | Login Page Tab | `loginDisclaimer.enabled`        | Show acceptance dialog before login | Low - Legal compliance | Based on requirements         |
| Disclaimer Expiration  | Login Page Tab | `loginDisclaimer.expirationDays` | Frequency of re-acceptance          | Low                    | 30-90 days                    |
| Do Not Skip Login Page | Login Page Tab | `doNotSkipLoginPage`             | Always show login page              | Low - User experience  | Enable if disclaimer required |

#### User Security Settings

| Setting             | UI Location             | API Field           | Function                             | Security Impact              | Recommended Value        |
| ------------------- | ----------------------- | ------------------- | ------------------------------------ | ---------------------------- | ------------------------ |
| User Alert Settings | User Alert Settings Tab | Alert configuration | Email notifications for login events | Medium - User awareness      | Enable for failed logins |
| Visibility          | Settings Tab            | `visibility`        | Public/private tenant discovery      | Low - Information disclosure | `private` for production |

#### Application Security Settings

| Setting                      | UI Location     | API Field                 | Function                          | Security Impact                     | Recommended Value               |
| ---------------------------- | --------------- | ------------------------- | --------------------------------- | ----------------------------------- | ------------------------------- |
| Client Authentication Method | Client settings | `tokenEndpointAuthMethod` | How clients authenticate          | High - Client security              | `private_key_jwt` (most secure) |
| Access Token Lifetime        | Client settings | `accessTokenLifetime`     | Access token validity             | Medium - Session exposure           | 900-3600 (15-60 min)            |
| Redirect URI Validation      | Client settings | `redirectUris`            | Valid OAuth redirect destinations | Critical - OAuth security           | Explicit list; no wildcards     |
| PKCE Requirement             | Client settings | `requirePkce`             | Proof Key for Code Exchange       | High - Code interception prevention | `true` for public clients       |

#### Key Management Settings

| Setting                     | UI Location                  | API Field               | Function                  | Security Impact         | Recommended Value        |
| --------------------------- | ---------------------------- | ----------------------- | ------------------------- | ----------------------- | ------------------------ |
| JWKS Key Rotation           | Tenant JWKS Tab              | Key rotation operations | Rotate JWT signing keys   | High - Token security   | Every 90-180 days        |
| SAML Certificate Management | Tenant SAML Certificates Tab | Certificate operations  | Manage SAML signing certs | High - Federation trust | Rotate before expiration |

#### Audit Settings

| Setting              | UI Location  | API Field                        | Function                  | Security Impact             | Recommended Value |
| -------------------- | ------------ | -------------------------------- | ------------------------- | --------------------------- | ----------------- |
| Audit Log Age Limit  | Settings Tab | `customTrimJobSettings.ageLimit` | Days to retain audit logs | Medium - Compliance         | 365+ days         |
| Audit Log Trim Ratio | Settings Tab | `trimBySize` settings            | Volume-based trimming     | Medium - Storage management | Based on volume   |

---

### Delegated Admin Security Settings Reference

The following capabilities are available to users with specific `admin::*` permissions.

#### Admin Scope Capabilities

| Scope                           | Capability               | Available Operations                                    | Security Impact                    |
| ------------------------------- | ------------------------ | ------------------------------------------------------- | ---------------------------------- |
| `admin::admin-permissions:edit` | Privileged RBAC entities | Create/modify privileged groups, roles, permissions     | High - Authorization control       |
| `admin::system-templates:edit`  | System templates         | Create/modify system HTML and email templates           | Medium - Branding/phishing risk    |
| `admin::trusted-provider:edit`  | Trusted IdP management   | Create/modify trusted IdPs, secondary unique attributes | Critical - Authentication trust    |
| `admin::provider-scripts:edit`  | Claims mapping           | Configure IdP claims mapping scripts                    | High - Authorization mapping       |
| `admin::view:idp-secrets`       | IdP secrets visibility   | View unmasked IdP secrets in API responses              | High - Credential exposure         |
| `admin::access-scripts:edit`    | SAML scripts             | Modify SAML access scripts                              | High - Authorization logic         |
| `admin::clients:edit`           | Client management        | Update client applications                              | Medium - Application configuration |
| `admin::user-ssh-secret:edit`   | SSH management           | Create/update SSH access requests                       | Medium - Infrastructure access     |

#### UMRS Configuration

| Setting             | Description                       | Security Impact            | Recommendation              |
| ------------------- | --------------------------------- | -------------------------- | --------------------------- |
| Role Definition     | What access is granted            | High - Scope of delegation | Define narrowly             |
| Manager Group       | Who can grant the role            | High - Delegation control  | Limit to trusted managers   |
| Expiration Required | Time-limited grants               | Medium - Access duration   | Enable for temporary access |
| Extension Requests  | Allow users to request extensions | Low - Workflow flexibility | Based on requirements       |

---

## Recommended Secure Configuration

### Secure Defaults

The Authifi service provisions new tenants with secure defaults. The following table shows defaults and recommended production values.

| Setting                     | Default Value       | Recommended Production Value | Notes                             |
| --------------------------- | ------------------- | ---------------------------- | --------------------------------- |
| Session Lifetime            | 3600 sec (1 hr)     | 14400-28800 sec (4-8 hr)     | Shorter for high-security tenants |
| Idle Session Timeout        | 1200 sec (20 min)   | 1800-3600 sec (30-60 min)    | Shorter for admin contexts        |
| Refresh Token Lifetime      | 20160 min (14 days) | 10080-20160 min (7-14 days)  | Shorter for sensitive apps        |
| Enable Admin MFA            | `false`             | `true`                       | **Enable immediately**            |
| Always Require MFA When Set | `true`              | `true`                       | Maintain default                  |
| TOTP Suspension Threshold   | 5                   | 5                            | Failed attempts before suspension |
| TOTP Lockout Threshold      | 10                  | 10                           | Failed attempts before lockout    |
| TOTP Suspension Period      | 2 min               | 2-5 min                      | Suspension duration               |
| Allow-list Enabled          | `true`              | `true`                       | **Never disable in production**   |
| Discovery Mode              | `false`             | `false`                      | Only for troubleshooting          |
| Audit Log Retention         | 360 days            | 365+ days                    | Compliance requirement            |

### Configuration Comparison API

**Endpoint**: `GET /auth/admin/tenants/{tenantId}`

**UI**: Tenant > Settings (view current configuration)

Use this endpoint to retrieve current tenant configuration for comparison against recommended baselines. The response includes security-relevant settings within the tenant object.

**Security-Relevant Fields in Response**:

```json
{
  "id": "example-tenant",
  "sessionLifetime": 28800,
  "idleSessionLifetime": 3600,
  "refreshTokenLifetime": 2592000,
  "enableAdminMfa": true,
  "requireMfaWhenSet": true,
  "totpSuspensionThreshold": 5,
  "totpLockoutThreshold": 10,
  "totpSuspensionPeriod": 2,
  "allowListEnabled": true,
  "discoveryMode": false,
  "customTrimJobSettings": {
    "ageLimit": 365
  }
}
```

### Configuration Export

**Tenant Configuration Export**:

- **UI**: Tenant > Settings > Export
- **Format**: JSON (machine-readable)
- **Note**: Tenant configuration is exported via the UI; use `GET /auth/admin/tenants/{tenantId}` API for programmatic access

**Audit Log Export**:

- **UI**: Monitoring > Audit Logs > Export Audit Logs
- **API**:
    - `GET /auth/admin/tenants/{tenantId}/audit/export/csv`
    - `GET /auth/admin/tenants/{tenantId}/audit/export/json`

---

## Security Best Practices

### Super Administrator Security

| Practice           | Description                                      | Priority |
| ------------------ | ------------------------------------------------ | -------- |
| Minimize count     | Limit to 2-5 individuals maximum                 | Critical |
| Dedicated accounts | Separate from day-to-day user accounts           | Critical |
| Hardware MFA       | Require WebAuthn/FIDO2 (TOTP minimum)            | Critical |
| Trusted IdP only   | Authenticate only via trusted identity providers | Critical |
| Short sessions     | 2-4 hour session lifetime for admin work         | High     |
| Secure workstation | Company-managed devices only; avoid public WiFi  | High     |
| Alert monitoring   | Review security alerts within 24 hours           | High     |
| Regular review     | Quarterly audit of super admin assignments       | Medium   |

### Tenant Administrator Security

| Practice               | Description                               | Priority |
| ---------------------- | ----------------------------------------- | -------- |
| Enable Admin MFA       | Set `enableAdminMfa: true`                | Critical |
| Group-based assignment | Use admin groups, not direct permissions  | High     |
| Regular reviews        | Quarterly audit of admin group membership | High     |
| Session management     | Use short idle timeouts (30-60 min)       | Medium   |
| Least privilege        | Use delegated admin where possible        | Medium   |

### Identity Provider Security

| Practice               | Description                              | Priority |
| ---------------------- | ---------------------------------------- | -------- |
| Trusted IdP for admins | Only use trusted IdPs for admin accounts | Critical |
| IdP-level MFA          | Configure MFA at identity provider       | High     |
| Claims validation      | Validate all mapped claims defensively   | High     |
| Regular testing        | Test IdP connections regularly           | Medium   |

### Application Security

| Practice               | Description                      | Priority |
| ---------------------- | -------------------------------- | -------- |
| `private_key_jwt` auth | Use for confidential clients     | High     |
| Explicit redirect URIs | No wildcards; exact matches only | High     |
| Short token lifetimes  | 15-60 minute access tokens       | Medium   |
| PKCE required          | Enable for all OAuth flows       | High     |
| Secret rotation        | Rotate client secrets quarterly  | Medium   |

### Secret Management

| Practice           | Description                         | Priority |
| ------------------ | ----------------------------------- | -------- |
| Sensitive type     | Use for all credentials             | High     |
| Set expiration     | 90-180 days for credentials         | High     |
| Immediate rotation | Rotate on suspected compromise      | Critical |
| Access auditing    | Review secret access logs regularly | Medium   |

---

## Compliance Requirements

### FedRAMP Recommended Secure Configuration Mapping

| Requirement                                           | Section                                                                                                                                                                                            | Coverage                                         |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| **FRR-RSC-01**: Top-Level Admin Accounts Guidance     | [Super Administrators](#super-administrators-top-level-administrative-accounts), [Super Administrator Lifecycle](#super-administrator-lifecycle)                                                   | Access, configuration, operation, decommission   |
| **FRR-RSC-02**: Top-Level Admin Security Settings     | [Super Administrator Security Settings Reference](#super-administrator-security-settings-reference)                                                                                                | All super admin-only settings and implications   |
| **FRR-RSC-03**: Privileged Accounts Security Settings | [Tenant Administrator Security Settings Reference](#tenant-administrator-security-settings-reference), [Delegated Admin Security Settings Reference](#delegated-admin-security-settings-reference) | All privileged account settings and implications |
| **FRR-RSC-04**: Secure Defaults on Provisioning       | [Recommended Secure Configuration](#recommended-secure-configuration)                                                                                                                              | Default values table                             |
| **FRR-RSC-05**: Comparison Capability                 | [Configuration Comparison API](#configuration-comparison-api)                                                                                                                                      | API endpoint for comparison                      |
| **FRR-RSC-06**: Export Capability                     | [Configuration Export](#configuration-export)                                                                                                                                                      | UI and API export                                |
| **FRR-RSC-07**: API Capability                        | [API Documentation](#api-based-management)                                                                                                                                                         | REST API for all settings                        |
| **FRR-RSC-08**: Machine-Readable Guidance             | This document (Markdown), API (JSON)                                                                                                                                                               | Machine-readable formats                         |
| **FRR-RSC-09**: Publish Guidance                      | GitHub repository                                                                                                                                                                                  | Publicly accessible                              |
| **FRR-RSC-10**: Versioning and Release History        | [Document Information](#document-information)                                                                                                                                                      | Version controlled                               |

### API-Based Management

All security settings are accessible via REST API and the Authifi UI:

| Resource           | Endpoints                                                               | UI Location                               |
| ------------------ | ----------------------------------------------------------------------- | ----------------------------------------- |
| Tenants            | `GET/PUT/PATCH /auth/admin/tenants/{tenantId}`                          | Tenant > Settings                         |
| Identity Providers | `GET/POST/PUT/DELETE /auth/admin/tenants/{tenantId}/identity-providers` | SSO Integration > Identity Provider Dashboard |
| Applications       | `GET/POST/PUT/DELETE /auth/admin/tenants/{tenantId}/clients`            | SSO Integration > App Dashboard           |
| Users              | `GET/POST/PUT/DELETE /auth/admin/tenants/{tenantId}/users`              | Users and Groups > Users                  |
| Groups             | `GET/POST/PUT/DELETE /auth/admin/tenants/{tenantId}/groups`             | Users and Groups > Groups                 |
| Allowed Origins    | `GET/POST/DELETE /auth/admin/tenants/{tenantId}/allowed-origins`        | SSO Integration > App Dashboard > Edit app > Allowed Origins |
| Audit Logs         | `GET /auth/admin/tenants/{tenantId}/audit-logs`                         | Resources and Tools > Audit Logs          |

**Complete API Documentation**: Available at `/auth/docs` (Swagger/OpenAPI) on Authifi service instances.

### Audit Checklists

**Super Administrator Audit (Quarterly)**:

- [ ] List all `systemAdmins` group members
- [ ] Verify business justification for each
- [ ] Confirm MFA enrollment (WebAuthn or TOTP)
- [ ] Review super admin actions in last 90 days
- [ ] Check for suspicious activity
- [ ] Verify no unauthorized privilege escalations
- [ ] Confirm security alert system functioning
- [ ] Document findings and remediation

**Tenant Administrator Audit (Quarterly)**:

- [ ] List all tenant admin group memberships
- [ ] Verify assignments are current
- [ ] Check for departing employees still in groups
- [ ] Review admin actions in last 90 days
- [ ] Verify MFA enrollment for admins
- [ ] Document findings

**Identity Provider Audit (Monthly)**:

- [ ] List all configured identity providers
- [ ] Verify "trusted" flag appropriateness
- [ ] Check MFA configuration at IdP level
- [ ] Review claims mapping and scripting
- [ ] Test IdP connections
- [ ] Document any issues

---

## Document Maintenance

| Activity             | Frequency     | Description                                     |
| -------------------- | ------------- | ----------------------------------------------- |
| Technical review     | Quarterly     | Verify accuracy of settings and recommendations |
| Comprehensive review | Annually      | Full security review and update                 |
| Post-incident update | As needed     | Update after security incidents                 |
| Version update       | With releases | Update for new features/changes                 |

---

## Change History

| Version | Date       | Changes         | Author                     |
| ------- | ---------- | --------------- | -------------------------- |
| 1.0     | 2026-01-22 | Initial release | Authifi Documentation Team |

---

## Additional Resources

**Internal Documentation**:

- [Recommended Secure Configuration](./recommended-secure-configuration.md) - Detailed security configuration
- [Super Admin Access Requirements](../authorization/super-admin-access.md) - Complete super-admin-only operations
- [Admin Roles Overview](../authorization/admin-roles.md) - Role hierarchy and enforcement
- [Tenant Administrator Guide](../guides/tenant-admin-guide.md) - Tenant settings configuration
- [Users and Groups Management Guide](../guides/users-groups-admin-guide.md) - User/group management
- [Monitoring and Logging Guide](../guides/monitoring-guide.md) - Audit logs and monitoring

**External Resources**:

- FedRAMP Recommended Secure Configuration: https://www.fedramp.gov/docs/rev5/recommended-secure-configuration/
- NIST SP 800-63B: Digital Identity Guidelines (Authentication)
- OWASP Authentication Cheat Sheet

**Support**:

- Documentation feedback: [GitHub Issues](https://github.com/AxleResearch/authifi-docs/issues)
- Security concerns: security@axleinfo.com
- General support: support@axleinfo.com

---

**Classification**: Public  
**Distribution**: Unlimited  
**URL**: https://docs.authifi.io/security/security-admin-guide

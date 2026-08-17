# SSO Integration Guide: Managing Applications and Identity Providers

Tenant administrators use the Authifi UI to manage SSO integrations, applications, APIs, and identity providers.

## Table of Contents

- [Overview](#overview)
- [SSO Integration Menu Options](#sso-integration-menu-options)
  - [App Dashboard](#app-dashboard)
  - [Contacts](#contacts)
  - [Access Roles (API Roles)](#access-roles-api-roles)
  - [Resource Server Permissions](#resource-server-permissions)
  - [API Dashboard](#api-dashboard)
  - [Providers](#providers)
  - [Issuers](#issuers)
  - [Namespaces](#namespaces)
- [Application (Client) Configuration Reference](#application-client-configuration-reference)
  - [Settings Section](#settings-section)
  - [Metadata Section](#metadata-section)
  - [Default User Groups](#default-user-groups)
  - [Custom Integrations](#custom-integrations)
  - [Certificates](#certificates)
  - [Groups](#groups)
  - [Contacts](#contacts-application-specific)
  - [Allowed Origins](#allowed-origins-application-specific)
  - [Script](#script)
- [Identity Provider Configuration](#identity-provider-configuration)
- [Security Best Practices](#security-best-practices)

---

## Overview

Use **SSO Integration** to:

- Configure applications (clients) that use your tenant for authentication
- Manage APIs (resource servers) and their access control
- Set up identity providers for user authentication
- Manage **Access Roles** and **Resource Server Permissions** for client-linked and standalone resource servers
- Manage multi-tenancy and namespace isolation

Misconfigured clients can lead to token theft, unauthorized access, or data leakage.

---

## SSO Integration Menu Options

### App Dashboard

**Location**: SSO Integration > App Dashboard

Use the App Dashboard to manage OAuth 2.0/OIDC clients and SAML service providers in your tenant.

#### What you can do

- **View all applications**: Table displays all configured clients with their names, client IDs, assigned roles/permissions, and type
- **Create new applications**: Multiple creation options:
  - Simple setup wizards (recommended for common scenarios)
  - Full configuration (advanced use cases)
  - Import from JSON
- **Edit applications**: Click settings icon to open full configuration dialog
- **Delete applications**: Remove unused or decommissioned apps
- **Search and filter**: Find apps by name, client ID, or type
- **Export configuration**: Export app settings as JSON for backup or migration

#### Application Types

- **OIDC Web App**: Server-side web applications (authorization code flow)
- **OIDC SPA**: Single-page applications (PKCE flow)
- **OIDC Native**: Mobile and desktop apps (PKCE flow)
- **OIDC M2M**: Machine-to-machine (client credentials flow)
- **SAML**: SAML 2.0 service providers

#### Creating a New Application

- Click **ADD NEW** dropdown
- Select application type:
    - **Simple Confidential**: Quick setup for server-side web apps
    - **Simple Public**: Quick setup for SPAs/mobile apps
    - **Advanced**: Full configuration options
- Fill in required information (see [Application Configuration Reference](#application-client-configuration-reference))
- Click **Save**

**Security recommendations**:

- Use **Simple Confidential** for server-side apps (more secure than public clients)
- Enable PKCE for all SPAs and mobile apps
- Restrict redirect URIs to known, trusted endpoints
- Use short-lived access tokens (300-900 seconds for most use cases)
- Rotate client secrets regularly for confidential clients

---

### Contacts

**Location**: SSO Integration > Contacts

Manage contact persons and associate them with applications for support and operational communications.

#### What you can do

- **Create contacts**: Add contact records with name, email, phone
- **Assign contacts to applications**: Link contacts to one or more apps
- **Categorize contacts**: Technical, security, billing, etc.
- **Update contact information**: Keep records current
- **View contact assignments**: See which apps each contact is responsible for

#### Use cases

- **Incident response**: Quick access to responsible parties during outages
- **Security notifications**: Contact technical leads about vulnerabilities
- **Change management**: Notify owners before making breaking changes
- **Compliance**: Document responsible parties for audit purposes

**Best practice**: Assign at least one technical contact to every production application.

---

### Access Roles (API Roles)

**Location**: SSO Integration > Access Roles (some screens still label these **API Roles**)

Define authorization roles that bundle Resource Server Permissions within a resource-server authorization context. Authifi uses one Access Role model for:

- **Client-linked resource servers** — including the auto-generated placeholder resource server associated with an OAuth client (application authorization context)
- **Standalone API resource servers** — registered APIs (API authorization context)

**Client permissions** are Resource Server Permissions on an OAuth client's auto-generated placeholder resource server. Define and assign those permissions (and Access Roles that include them) when the application needs its own authorization context.

**Client credential roles and permissions** mean Access Roles marked for client credential grants, their Resource Server Permissions, and the associated client. This authorization is assigned to applications rather than users.

#### What you can do

- **Create Access Roles**: Define roles like "viewer", "editor", "admin" for a resource server context
- **Assign permissions**: Group Resource Server Permissions into a role
- **Assign to users/groups**: Grant roles to groups (via Users and Groups); group members inherit the roles
- **View role assignments**: See which groups have which roles
- **Delete unused roles**: Clean up role definitions

#### Role vs. Permission

- **Resource Server Permission**: Fine-grained access right (e.g., `read:users`, `write:documents`) scoped to a resource server
- **Access Role**: Collection of Resource Server Permissions within one resource-server context

**Example role structure**:

```
viewer:
  - read:users
  - read:documents

editor:
  - read:users
  - read:documents
  - write:documents

admin:
  - read:*
  - write:*
  - delete:*
  - manage:users
```

#### Retrieving Access Roles via UserInfo

For new integrations, request `profile` and read `resource_roles` from trusted UserInfo or server-side session data. Roles are keyed by resource server identifier. Ordinary API access tokens are authorized with permission `scope`; do not treat `resource_roles` or `groups` as ordinary access-token claims.

```
GET /authorize?
  response_type=code&
  client_id=YOUR_CLIENT_ID&
  scope=openid profile&
  redirect_uri=YOUR_REDIRECT_URI
```

Example UserInfo / session claim shape:

```json
{
  "sub": "user-id",
  "name": "Jane Doe",
  "resource_roles": {
    "https://api.example.com": ["editor", "reviewer"]
  }
}
```

> **Compatibility**: Flat `roles` / `access_roles` claims and deprecated `/roles` admin routes are legacy-only. See [Roles and Permissions Compatibility](../authorization/legacy-roles-permissions-compatibility.md).

**Security recommendations**:

- Follow principle of least privilege
- Create specific roles rather than overly broad ones
- Review role assignments quarterly
- Document what each role grants access to

---

### Resource Server Permissions

**Location**: SSO Integration > Permissions (or the Permissions tab on a resource server / API)

Define fine-grained permissions for a resource server. They map to OAuth 2.0 scopes in API access tokens.

#### What you can do

- **Create permissions**: Define granular access rights for a resource server
- **Assign to Access Roles**: Build roles from individual permissions
- **Control access tokens**: Granted permissions appear in the space-separated `scope` claim on ordinary API access tokens

#### Permission Naming Convention

Use a consistent naming scheme:

- **Format**: `<action>:<resource>`
- **Examples**:
  - `read:users`
  - `write:documents`
  - `delete:records`
  - `admin:tenant`

#### OAuth 2.0 Scopes

Resource Server Permissions map to OAuth 2.0 scopes. When authorizing API calls, validate the access token and enforce the required permission from its `scope` claim.

**Security recommendations**:

- Use clear, descriptive permission names
- Avoid overly broad permissions (e.g., `admin:*`)
- Prefer separate permission sets per resource server to avoid name collisions across APIs
- Audit permission grants regularly

---

### API Dashboard

**Location**: SSO Integration > API Dashboard

Manage resource servers (APIs) that applications can access on behalf of users.

#### What you can do

- **Create APIs**: Register new resource servers
- **Configure access tokens**: Set token lifetime and claims
- **Assign clients**: Control which apps can access the API
- **Define Access Roles and Resource Server Permissions**: Create RBAC for each API resource server
- **Add custom claims**: Include additional data in access tokens
- **Export configuration**: Back up or migrate API settings

#### Creating a New API

- Click **ADD NEW**
- Enter API details:
    - **Name**: Human-readable name (e.g., "User Management API")
    - **Identifier**: Unique URI (e.g., "https://api.example.com/users")
        - This value becomes the `aud` (audience) claim in tokens and the key for `resource_roles`
    - **Access Token Duration**: Token lifetime in seconds (default: 86400 = 24 hours)
- Assign client applications that should have access
- Configure custom claims (optional, super admin only)
- Create Access Roles and Resource Server Permissions for this API (see [Access Roles](#access-roles-api-roles) and [Resource Server Permissions](#resource-server-permissions))
- Click **Save**

**Security recommendations**:

- Use HTTPS URIs for identifiers
- Set token lifetime based on API sensitivity (shorter for high-risk APIs)
- Implement token validation in your API (verify signature, audience, expiration)
- Enforce required permissions from the access token `scope` claim

---

### Providers

**Location**: SSO Integration > Providers

Configure identity providers (IdPs) for user authentication, including Google, Azure AD, and SAML providers.

#### What you can do

- **Add identity providers**: Configure social, enterprise, or custom IdPs
- **Simple setup**: Quick configuration for Google and Azure
- **Advanced setup**: Full control for SAML, OIDC, OAuth2
- **Import/Export**: Migrate IdP configurations
- **Clone providers**: Duplicate and modify existing configurations
- **Test connections**: Verify IdP integration before enabling
- **Enable/disable**: Toggle IdP availability
- **Set MFA requirements**: Enforce multi-factor authentication
- **Configure claims mapping**: Map IdP attributes to user profiles

#### Supported Provider Types

- **OAuth 2.0**: Generic OAuth2 providers
- **OIDC**: OpenID Connect providers
- **SAML 2.0**: Enterprise SAML identity providers
- **Google**: Google Workspace / Gmail
- **Azure AD**: Microsoft Azure Active Directory
- **LDAP**: Lightweight Directory Access Protocol
- **Local**: Username/password (managed by Authifi service, not suitable for production)

#### Adding a New Provider

**Simple Setup (Google)**:

- Click **ADD NEW** > **Simple Google**
- Enter:
    - **Display Name**: Shown to users (e.g., "Sign in with Google")
    - **Client ID**: From Google Cloud Console
    - **Client Secret**: From Google Cloud Console
- Configure:
    - **Enabled**: Toggle to activate
    - **MFA Type**: Optional (TOTP, Passkey)
    - **Is Trusted**: Mark as verified IdP (affects MFA requirements)
- Click **Save**

**Advanced Setup (Generic OIDC)**:

- Click **ADD NEW** > **Other**
- Select **Type**: "oidc"
- Configure:
    - **Display Name**: User-facing name
    - **Identifier**: Unique name for routing
    - **Issuer URL**: IdP's OpenID configuration endpoint
    - **Client ID & Secret**: From IdP
    - **Scopes**: openid, profile, email (minimum)
    - **Claims Mapping**: Map IdP claims to user attributes
- Test connection
- Click **Save**

**SAML Configuration**:

- Click **ADD NEW** > **SAML**
- Configure:
    - **Display Name**: User-facing name
    - **Entity ID**: IdP's entity identifier
    - **SSO URL**: IdP's single sign-on endpoint
    - **Certificate**: IdP's X.509 signing certificate
    - **Attribute Mapping**: Map SAML assertions to user attributes
- Download Authifi metadata XML
- Upload metadata to your IdP
- Test SAML flow
- Click **Save**

#### Provider Security Settings

- **Is Trusted**: Marks provider as verified/enterprise-grade
  - Affects: MFA requirements, admin access, trust boundaries
  - **Recommendation**: Only enable for organizationally-controlled IdPs
- **MFA Type**: Enforce MFA at provider level
  - **TOTP**: Time-based one-time passwords
  - **Passkey**: WebAuthn/FIDO2 (phishing-resistant)
- **AAL Override**: Override Authenticator Assurance Level
  - **Super admin only**: Requires elevated privilege
- **Claims Scripting**: Custom JavaScript for claims transformation
  - **Super admin only**: High-risk feature

**Security recommendations**:

- **Use trusted IdPs for admin accounts** (marked as "Is Trusted")
- **Enable MFA** for all production providers
- **Validate IdP certificates** (for SAML) to prevent MITM attacks
- **Restrict callback URLs** to prevent token theft
- **Use separate providers** for different security levels (e.g., employee vs. customer IdPs)
- **Test thoroughly** in non-production environment before enabling
- **Monitor authentication logs** for unusual patterns
- **Rotate IdP credentials** regularly (client secrets, certificates)

---

### Issuers

**Location**: SSO Integration > Issuers

Manage OAuth 2.0/OIDC issuer configurations for token generation and validation.

#### What you can do

- **View issuers**: See configured token issuers
- **Create issuers**: Define new token issuing authorities (super admin)
- **Configure token settings**: Adjust token algorithms, lifetimes, claims
- **Manage issuer keys**: Rotate signing keys (JWKS)
- **Set token policies**: Control refresh tokens, consent requirements

#### Issuer Configuration

An issuer defines:

- **Issuer URL**: The `iss` claim in tokens (e.g., `https://auth.example.com`)
- **Signing Algorithm**: RSA256, ES256, etc.
- **Key Rotation Policy**: How often signing keys change
- **Token Defaults**: Default lifetimes, audiences, claims

**Use case**: Multi-tenant deployments may use different issuers for different tenant classes (e.g., free vs. enterprise).

**Security note**: Issuer configuration typically requires super admin privileges. Misconfigurations can invalidate all tokens.

---

### Namespaces

**Location**: SSO Integration > Namespaces

Use namespaces as logical isolation boundaries for multi-tenant or multi-environment resources.

#### What you can do

- **Create namespaces**: Define logical boundaries (e.g., "prod", "dev", "customer-A")
- **Assign resources**: Associate apps, APIs, users, groups with namespaces
- **Enforce boundaries**: Prevent cross-namespace access
- **Manage quotas**: Set resource limits per namespace
- **View namespace usage**: Monitor resource consumption

#### Use Cases

- **Environment separation**: Isolate dev, staging, production
- **Customer segmentation**: Separate data for different customers in multi-tenant SaaS
- **Business unit isolation**: Different departments with independent resources
- **Compliance boundaries**: Separate regions/jurisdictions (GDPR, HIPAA)

**Example namespace structure**:

```
root
├── production
│   ├── apps: [web-app, mobile-app, admin-portal]
│   └── apis: [user-api, order-api, payment-api]
├── staging
│   ├── apps: [web-app-staging, mobile-app-staging]
│   └── apis: [user-api-staging]
└── development
    └── apps: [web-app-dev]
```

**Security recommendations**:

- Use namespaces to enforce least privilege
- Separate production from non-production namespaces
- Apply different security policies per namespace (e.g., stricter token lifetimes in prod)
- Audit cross-namespace access attempts

---

## Application (Client) Configuration Reference

When you create or edit an application in the **App Dashboard**, its configuration dialog contains these sections.

### Settings Section

Configure the client type, authentication flows, and token settings.

#### Basic Settings

- **Display Name\***
  - User-facing name shown during authentication
  - Example: "My Company Web App"
- **Name**
  - Internal identifier (read-only after creation)
  - Auto-generated from display name

- **Client ID**
  - Unique identifier for OAuth 2.0/OIDC flows
  - Read-only (auto-generated)

- **Client Secret** (Confidential clients only)
  - Secret key for authenticating the client to the Authifi service
  - **Critical security**: Treat like a password, never expose in client-side code
  - Rotate regularly (recommendation: every 90-180 days)

- **Type\***
  - **oidc**: OpenID Connect (most common)
  - **saml**: SAML 2.0 service provider
  - Selection affects available configuration options

- **Application Type\***
  - **web**: Server-side web application (authorization code flow)
    - Uses client secret
    - Most secure for traditional web apps
  - **spa**: Single-page application
    - No client secret (public client)
    - Requires PKCE
  - **native**: Mobile or desktop application
    - No client secret
    - Requires PKCE
  - **m2m**: Machine-to-machine (service account)
    - Uses client credentials flow
    - No user interaction

#### Authentication Flow Settings

- **Grant Types**
  - **authorization_code**: Standard OAuth 2.0 flow (recommended)
  - **implicit**: Legacy, not recommended (security risk)
  - **client_credentials**: For M2M applications
  - **refresh_token**: Enable refresh token issuance
  - **password**: Resource Owner Password Credentials (avoid if possible)

- **Response Types**
  - **code**: Authorization code (most secure)
  - **token**: Implicit flow (deprecated, security risk)
  - **id_token**: OpenID Connect ID token
  - **code id_token**: Hybrid flow

**Security recommendation**: Use `authorization_code` grant type with PKCE for all client types except M2M.

#### Redirect URIs

- **Purpose**: Whitelist of allowed callback URLs after authentication
- **Format**: Exact match (no wildcards in production)
- **Examples**:
  - `https://myapp.example.com/callback`
  - `http://localhost:3000/callback` (dev only)
  - `com.myapp.mobile://callback` (mobile deep link)

**Security requirements**:

- **Use HTTPS** in production (not HTTP)
- **No wildcards** (exact match only)
- **No open redirects** (restrict to known endpoints)
- **Validate strictly**: Authifi rejects tokens sent to non-whitelisted URIs

#### Logout URIs

- **Post Logout Redirect URIs**: Where to send users after logout
- **Same security requirements** as redirect URIs

#### Token Configuration

- **Access Token Lifetime** (seconds)
  - Default: 900 (15 minutes)
  - Range: 120 - 86400 (2 minutes to 24 hours)
  - **Recommendation**: 300-900 seconds for most apps

- **Refresh Token Lifetime** (minutes)
  - Default: 20,160 (14 days)
  - **Recommendation**: 7-30 days depending on security requirements

- **ID Token Lifetime** (seconds)
  - Default: 3600 (1 hour)
  - ID tokens are short-lived by design

- **Absolute Session Lifetime** (seconds)
  - Maximum session duration regardless of activity
  - Overrides refresh token behavior

- **Inactivity Session Lifetime** (seconds)
  - Idle timeout
  - Minimum: 120 seconds

#### PKCE Settings (Public Clients)

- **PKCE Required**
  - **Always enable** for SPAs and mobile apps
  - Prevents authorization code interception attacks
  - **Standard**: S256 (SHA-256 challenge method)

#### Consent Settings

- **Require Consent**
  - Force user to approve permission grants
  - **Recommendation**: Enable for third-party apps, optional for first-party apps

- **Show Consent on**: First use, every time, or custom rules

#### Additional Settings

- **Enable Client Authentication**
  - Require client secret for token requests (confidential clients only)

- **Enable Audience Restrictions**
  - Limit which APIs this client can access

- **Backchannel Logout**
  - Support OpenID Connect Back-Channel Logout specification

---

### Metadata Section

Add client information for documentation and management.

- **Description**: Detailed description of the application's purpose
- **Client Type**: Categorization (internal, external, partner)
- **Owner Email**: Contact for the application owner
- **License Assignment**: Link to license records (super admin only)
- **Tenant Assignment**: Associate with specific tenant (multi-tenant setups)

---

### Default User Groups

Assign users to groups automatically after their first authentication through this application.

#### Configuration

- **Auto-Assign Groups**: List of groups to add users to
- **Use Case**: Onboard new users automatically with baseline permissions

**Example**: New users signing in to "Employee Portal" are automatically added to "employees" group, which grants basic access rights.

**Security note**: Use carefully to avoid inadvertently granting excessive permissions.

---

### Custom Integrations

Configure application-specific integrations and extensions.

**Available integrations** (varies by application type):

- **Webhooks**: Subscribe to authentication events
- **Custom Claims**: Add application-specific data to tokens
- **Post-Authentication Scripts**: Execute custom logic after user signs in
- **Metadata Endpoints**: Expose application-specific metadata

**Security warning**: Custom integrations require careful validation to avoid introducing vulnerabilities.

---

### Certificates

Manage X.509 certificates for SAML and mutual TLS authentication.

#### SAML Certificates

- **Service Provider Certificate**: Used to sign SAML requests/responses
- **Upload**: PEM or DER format
- **Lifecycle**:
  1. Upload new certificate before expiration
  2. Test with new certificate
  3. Activate new certificate
  4. Remove old certificate

#### OAuth 2.0 Certificates (mTLS)

- **Client Certificate**: For mutual TLS client authentication
- **Use case**: High-security M2M applications

**Security best practices**:

- Monitor certificate expiration (check Expiring Items dashboard)
- Use strong key sizes (minimum 2048-bit RSA)
- Rotate certificates annually or per compliance requirements
- Store private keys securely (HSM recommended for production)

---

### Groups

Assign user groups that have access to this application.

#### Configuration

- **Assigned Groups**: Groups whose members can access the app
- **Default Access**: Whether all tenant users can access (public app) or only specified groups (private app)

**Use cases**:

- **Private app**: Limit "Admin Portal" to "admins" and "operators" groups
- **Public app**: "Employee Self-Service" available to all "employees" group

**Security recommendation**: Use group-based access control for all production applications; avoid "all users" access.

---

### Contacts (Application-Specific)

Assign contacts responsible for this application.

- **Technical Contact**: Person who maintains the application
- **Security Contact**: Person to notify about security issues
- **Business Owner**: Person responsible for business decisions

**Best practice**: Ensure every production application has at least a technical and security contact.

---

### Allowed Origins (Application-Specific)

Configure CORS allowed origins for this application.

**Note**: Typically managed at the tenant level (see Tenant Settings guide). Application-specific origins are less common.

**Use case**: Application needs unique CORS configuration separate from tenant defaults.

---

### Script

Map and transform custom claims with JavaScript.

**Availability**: System admin or users with `admin::clients:edit` only.

#### Claims Scripting

- **Custom Claims**: Add computed or mapped claims to tokens
- **Conditional Logic**: Include claims based on user properties or context
- **External Data**: Fetch additional data from external sources (with caution)

**Example use cases**:

- Add department information to tokens based on user email domain
- Include customer ID from external CRM system
- Add feature flags based on user subscription tier

**Security warnings**:

- **High risk**: Scripting can introduce vulnerabilities or performance issues
- **Validate inputs**: Never trust data from external sources
- **Limit complexity**: Keep scripts simple and fast
- **Test thoroughly**: Broken scripts can break authentication
- **Audit changes**: Log all script modifications
- **Restrict access**: Only grant scripting privilege to trusted admins

---

## Identity Provider Configuration

Provider configurations vary by identity provider. Refer to the provider-specific documentation:

- **Google**: See "Configuring Google OAuth" guide
- **Azure AD**: See "Configuring Azure AD OIDC" guide
- **SAML**: See "SAML Integration Guide"
- **Custom OIDC**: See "Generic OIDC Provider Setup"

---

## Security Best Practices

### Application Security

- **Use the principle of least privilege**
    - Grant only required permissions
    - Use fine-grained permissions over broad roles
    - Regularly review and revoke unnecessary access

- **Implement defense in depth**
    - Use PKCE for all public clients (SPAs, mobile)
    - Enable client authentication for confidential clients
    - Rotate secrets regularly
    - Implement token validation in your applications
    - Use short-lived access tokens

- **Secure redirect URIs**
    - Use exact match (no wildcards)
    - HTTPS only in production
    - Validate on both client and server side
    - Avoid open redirect vulnerabilities

- **Protect client secrets**
    - Never commit to source control
    - Store in secure vaults (e.g., AWS Secrets Manager, Azure Key Vault)
    - Rotate every 90-180 days
    - Monitor for exposure (secret scanning tools)

- **Monitor and audit**
    - Review audit logs regularly
    - Set up alerts for suspicious authentication patterns
    - Track token issuance and validation
    - Monitor for unusual permission grants

### Identity Provider Security

- **Vet providers carefully**
    - Only mark IdPs as "Is Trusted" when they are enterprise-managed and secured
    - Prefer enterprise IdPs over social for sensitive applications
    - Validate IdP certificates (SAML)
    - Test thoroughly before production

- **Enforce MFA**
    - Enable MFA requirements at provider level
    - Use phishing-resistant MFA (WebAuthn/FIDO2) where possible
    - Require MFA for admin accounts

- **Secure claims mapping**
    - Validate all IdP claims
    - Don't trust user-modifiable attributes for authorization decisions
    - Use immutable identifiers (subject/nameID, not email)
    - Test claims mapping with various user scenarios

- **Manage IdP certificates**
    - Monitor expiration
    - Rotate before expiration
    - Validate certificate chains
    - Use strong signing algorithms (RSA-2048 minimum, prefer RSA-4096 or ECC)

### API Security

- **Validate tokens**
    - Verify signature (use JWKS from issuer)
    - Check audience (`aud` claim matches your API identifier)
    - Verify expiration (`exp` claim)
    - Validate issuer (`iss` claim)

- **Implement scope-based authorization**
    - Check required scopes in API endpoints
    - Use least privilege (narrow scopes)
    - Separate read and write scopes
    - Document scope requirements

- **Use separate permission models**
    - Create API-specific permissions
    - Don't reuse app permissions for APIs
    - Use namespaced permissions (e.g., `api:users:read`)

- **Set appropriate token lifetimes**
    - Shorter for sensitive APIs (300-900 seconds)
    - Longer for low-sensitivity APIs (3600-7200 seconds)
    - Balance security with user experience

### Operational Security

- **Follow change management**
    - Test configuration changes in non-production first
    - Document all production changes
    - Have rollback plans
    - Notify affected parties before breaking changes

- **Maintain contact information**
    - Keep contacts current
    - Assign contacts to all applications
    - Document escalation procedures
    - Test communication channels

- **Regularly review configuration**
    - Quarterly access reviews
    - Remove unused applications, APIs, providers
    - Update outdated configurations
    - Refresh certificates before expiration

- **Backup and disaster recovery**
    - Export critical configurations regularly
    - Store backups securely
    - Test restoration procedures
    - Document recovery steps

---

## Additional Resources

- [Tenant Administrator Guide](tenant-admin-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)
- **Authifi API Documentation**: Available via Help > API Documentation in the UI

---

## Getting Help

For assistance with SSO integration:

- **Check audit logs** for error details
- **Test in non-production** before deploying to production
- **Review provider-specific documentation** for IdP configuration
- **Contact your super administrator** for elevated privilege requests
- **Consult API documentation** for advanced scenarios

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators

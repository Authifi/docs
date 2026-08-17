# Tenant Administrator Guide: Managing Tenant Settings

Tenant administrators manage tenant settings through the Authifi UI.

## Table of Contents

- [Overview](#overview)
- [Accessing Tenant Settings](#accessing-tenant-settings)
- [Tenant Menu Options](#tenant-menu-options)
  - [Dashboard](#dashboard)
  - [Expiring Items](#expiring-items)
  - [Settings (Edit Tenant Dialog)](#settings-edit-tenant-dialog)
  - [System Notifications](#system-notifications)
  - [Login Metrics](#login-metrics)
  - [Usage Report](#usage-report)
- [Edit Tenant Dialog - Tab Reference](#edit-tenant-dialog-tab-reference)
  - [Settings Tab](#settings-tab)
  - [Branding Tab](#branding-tab)
  - [Login Page Tab](#login-page-tab)
  - [Landing Page Tab](#landing-page-tab)
  - [Allowed Origins Tab](#allowed-origins-tab)
  - [User Alert Settings Tab](#user-alert-settings-tab)
  - [Email Settings Tab](#email-settings-tab)
  - [Tenant SAML Certificates Tab](#tenant-saml-certificates-tab)
  - [Tenant JWKS Key Set Tab](#tenant-jwks-key-set-tab)
  - [Cookie Consent Banner Tab](#cookie-consent-banner-tab)
  - [Contacts Tab](#contacts-tab)
- [Security Recommendations](#security-recommendations)

---

## Overview

**Tenant administrators** manage authentication and authorization settings, branding, and the user experience through the Authifi UI.

Some features require Super Administrator privileges or elevated admin scopes. The guide identifies those restrictions.

---

## Accessing Tenant Settings

- Log in to the Authifi UI
- Select your tenant from the tenant dropdown in the top navigation
- Navigate to the **Tenant** section in the left navigation menu
- Select the desired option from the submenu

The **Tenant** menu includes:

- Dashboard
- Expiring Items
- Settings
- System Notifications
- Login Metrics
- Usage Report

---

## Tenant Menu Options

### Dashboard

**Location**: Tenant > Dashboard

View your tenant's current status and key metrics.

**What you can do**:

- View tenant summary information
- Monitor key statistics
- Access quick links to common tasks

---

### Expiring Items

**Location**: Tenant > Expiring Items

Monitor items approaching expiration.

**What you can monitor**:

- Expiring certificates (SAML, X.509)
- Expiring access grants
- Other time-sensitive configurations

Review this page regularly to avoid service disruptions from expired credentials.

---

### Settings (Edit Tenant Dialog)

**Location**: Tenant > Settings

Configure tenant settings in a multi-tab dialog.

Click **Edit** on the Tenant Details page to open the tenant settings dialog. It contains 11 configuration tabs.

---

### System Notifications

**Location**: Tenant > System Notifications

Manage tenant-wide system notifications displayed to users.

**What you can do**:

- Create system-wide notifications
- Schedule notification display periods
- Configure notification visibility

**Security note**: Creating global (cross-tenant) notifications requires super admin privileges.

---

### Login Metrics

**Location**: Tenant > Login Metrics

View real-time and historical login metrics from Grafana.

**What you can see**:

- Login success/failure rates
- Authentication method usage
- Geographic distribution of logins
- Time-series charts and trends

Use this page to monitor unusual authentication patterns.

---

### Usage Report

**Location**: Tenant > Usage Report

View usage statistics for your tenant's SSO integrations.

**What you can see**:

- Active users per application
- Authentication volume by client
- Identity provider usage
- Session statistics

Use this page to tune SSO configuration based on usage patterns.

---

## Edit Tenant Dialog - Tab Reference

Open the Edit Tenant dialog from **Tenant > Settings** by clicking **Edit**.

### Settings Tab

**Purpose**: Configure core tenant properties and session management.

#### Basic Information

- **Display Name\*** (required)
  - The human-readable name shown to users
  - Must contain at least one alphabetic character
  - Used in UI displays and user-facing communications

- **Name**
  - The tenant's unique identifier (tenantId)
  - Read-only after creation
  - Maximum 50 characters

- **ID**
  - Internal numeric identifier
  - Read-only (automatically assigned)

- **Description\*** (required)
  - Detailed description of the tenant's purpose
  - Maximum 1024 characters
  - Helpful for documenting tenant usage

- **Visibility**
  - `Public`: Tenant is searchable by anonymous users
  - `Private`: Tenant is hidden from public tenant lists
  - **Security recommendation**: Use `Private` for production tenants unless public discovery is required

- **Logo**
  - Upload a custom logo for your tenant
  - Displayed in the tenant selector and branding elements
  - Supported formats: PNG, JPEG, SVG

#### Session Management

- **Session Lifetime** (seconds)
  - Overall maximum session duration
  - Default: 3600 seconds (1 hour)
  - Controls the absolute maximum time a user can remain authenticated
  - **Security recommendation**: Set based on your security requirements; shorter is more secure

- **Idle Session Lifetime** (seconds)
  - Timeout duration for inactive sessions
  - Minimum: 120 seconds (2 minutes)
  - Default: 1200 seconds (20 minutes)
  - Triggers automatic logout after period of inactivity
  - **Security note**: Users receive a warning 5 minutes before expiration (or 1 minute for sessions ≤5 minutes)

- **Refresh Token Lifetime** (minutes)
  - How long refresh tokens remain valid
  - Default: 20,160 minutes (14 days)
  - Allows users to obtain new access tokens without re-authenticating

- **Time Zone**
  - Tenant's default timezone for timestamps and scheduling
  - Used in audit logs, expiration calculations, and UI displays

#### Security Features

- **Enable Admin MFA**
  - Enforces multi-factor authentication for all tenant admins and system admins
  - **Security recommendation**: **Always enable** for production tenants
  - Applies tenant-wide to all users with admin privileges

- **Always Require MFA When Set**
  - Enforces MFA for all users who have already configured it
  - Applies to all clients in the tenant
  - **Use case**: Enforce consistent MFA usage after initial opt-in period

- **Enable TOTP Reset Requests**
  - Enables the TOTP reset request workflow
  - Allows users to request assistance with lost TOTP devices
  - Exposes TOTP reset endpoints and login features

- **TOTP Reset Link Expiry** (minutes)
  - How long TOTP reset links remain valid
  - Default: 15 minutes
  - Only visible when TOTP reset is enabled

- **TOTP Strike Count**
  - Maximum failed TOTP attempts before account lockout
  - Default: 5 attempts
  - **Security recommendation**: Keep at 3-5 attempts

- **TOTP Lockout Period** (minutes)
  - Duration of account lockout after exceeding strike count
  - Default: 2 minutes
  - **Security note**: Protects against brute-force attacks

#### Advanced Features

- **Enable Command Terminal**
  - Enables the web-based CLI terminal feature
  - **Availability**: Only shown if configured at the platform level
  - **Security note**: Grants direct CLI access; use with caution

- **User Supplied Attributes**
  - Define custom user attributes that clients can request
  - Configured as JSON array with validation rules
  - **Format**:
    ```json
    [
      {
        "name": "attribute_name",
        "required": true,
        "tooltip": "attribute tooltip",
        "validator": "text|number|date|email|url",
        "minLength": 4,
        "maxLength": 20,
        "inputType": "single|multi-line"
      }
    ]
    ```

#### Audit Log Retention Settings

- **Enable Custom trimByDate Trim Job Settings**
  - Customize audit log retention by age
  - **Age Limit** (days): Number of days to retain audit logs

- **Enable Custom trimBySize Trim Job Settings**
  - Customize audit log retention by volume
  - **Check Number**: Number of records to evaluate
  - **Trim Ratio**: Proportion of records to remove when threshold is reached (0.0 to 1.0)

**Security recommendation**: Balance compliance requirements with storage costs; retain audit logs for at least 90 days for most compliance frameworks.

---

### Branding Tab

Customize the visual appearance of your tenant's authentication pages and email communications.

**Availability**: Requires tenant admin or super admin privileges.

#### Tenant Icon

- **Purpose**: Logo displayed in tenant selector and branding elements
- **Supported formats**: PNG, JPEG, SVG
- **Recommended size**: 200x200 pixels minimum for optimal quality
- **Preview**: Live preview shows how the icon will appear

#### Email Branding

Configure the appearance of system-generated emails sent to users.

- **Email Header Background Color**
  - Background color for email header section
  - Default: `#01336f` (blue)
  - Visual color picker with live preview

- **Email Header Text Color**
  - Text color for email header
  - Default: `#ffffff` (white)
  - Ensure sufficient contrast with header background

- **Email Body Text Color**
  - Color for email body text
  - Default: `#333333` (dark gray)

- **Font Family**
  - Typography for email content
  - Default: `Roboto, Helvetica, Arial, sans-serif`
  - Options: System font stacks for cross-client compatibility

- **Font Size**
  - Base font size for email content
  - Default: `14px`

- **Email Header Title**
  - Title text displayed in email header
  - Default: `Single Sign-On System Message`
  - Maximum: 100 characters

- **Email Template Logo** (Super Admins Only)
  - Modify the logo used in email templates
  - **Restriction**: Only super administrators can edit system logo
  - Separate from tenant icon

**Email Preview**: Live preview pane shows how emails will appear with current settings.

**Best practices**:

- Test emails after changing branding settings
- Ensure colors meet WCAG accessibility guidelines (4.5:1 contrast ratio minimum)
- Use your organization's brand colors for consistency

---

### Login Page Tab

Customize the login page appearance and behavior.

**Availability**: Requires `auth.clients.list` or `auth.clients.update` scope (or tenant admin bypass).

#### Background and Panels

- **Background Images**
  - Upload one or more background images for login page rotation
  - Supports multiple images for variety
  - **Recommended size**: Full HD (1920x1080) or higher

- **Panel Banner**
  - Image displayed at the top of the login panel
  - Single image selection
  - **Use case**: Organization logo or branding header

- **Footer**
  - Image displayed in the login page footer
  - Single image selection
  - **Use case**: Partner logos, compliance badges

#### Disclaimer Settings

- **Enable Disclaimer**
  - Displays a mandatory acceptance dialog before login
  - **Use case**: Terms of service, acceptable use policies

- **Disclaimer Text**
  - Content of the disclaimer message
  - Maximum: 4000 characters
  - Supports plain text

- **Disclaimer Expiration Days**
  - How often users must re-accept the disclaimer
  - **Compliance note**: Some regulations require periodic re-acceptance

#### Page Text Customization

- **Title**
  - Main heading displayed on the login page
  - Example: "Welcome to [Organization] SSO"

- **Description**
  - Subheading or descriptive text
  - Example: "Sign in with your organizational credentials"

- **Login Options Title**
  - Text shown above identity provider buttons
  - Example: "Choose your login method"

#### Template Configuration

- **Do not skip login page**
  - When enabled, always shows the login page even when only one identity provider is configured
  - When disabled (default), users are redirected directly to the IdP if only one option exists
  - **Use case**: Enable if you need users to always see disclaimers or custom messaging

- **Select a template**
  - Choose from predefined HTML templates or create custom templates
  - **System templates**: Marked with a red "System" tag; only super admins can edit
  - **Actions**:
    - **Create** (+): Create a new HTML template
    - **Edit** (pencil): Modify the current template (disabled for system templates unless you're a super admin)
    - **Preview** (eye): Preview the template in a new window

- **Custom FooterLinks JSON**
  - Define footer links as a JSON array
  - **Format**:
    ```json
    [
      {
        "name": "text displayed for the link",
        "title": "title attribute",
        "href": "href attribute: the destination URL"
      }
    ]
    ```
  - **Example use cases**: Privacy policy, terms of service, support links

**Security recommendation**: Test login page changes in a non-production tenant first to avoid accidentally blocking user access.

---

### Landing Page Tab

Configure the post-authentication landing page.

**Availability**:

- May be restricted by global configuration
- Typical access: Tenant admins and/or super admins (configurable)
- The tab will be hidden if landing pages are globally disabled

**Note**: If you see an "Access Restricted" message when clicking this tab, landing page edits may be limited to super administrators only per global policy.

#### Landing Page Configuration

- **Name\*** (required)
  - Unique identifier for this landing page template
  - Read-only for system templates

- **Title**
  - Page title displayed in browser tab and header

- **Is System Template?**
  - Toggle to mark this template as a system template
  - **Restriction**: Requires super admin or `admin::system-templates:edit`
  - System templates can be shared across tenants

- **Custom FooterLinks JSON**
  - Same format as Login Page footer links
  - Allows users to navigate to documentation, dashboards, etc. after login

- **Template** (HTML)
  - Custom HTML for the landing page
  - **Monaco editor** with syntax highlighting and validation
  - **Preview button**: Opens a new window to preview the rendered page
  - **Best practice**: Use semantic HTML and ensure mobile responsiveness

**Use cases**:

- Welcome messages for users
- Links to applications and resources
- Onboarding instructions
- Organizational announcements

---

### Allowed Origins Tab

Manage Cross-Origin Resource Sharing (CORS) allowed origins for your tenant.

**Critical security feature**: Only origins listed here can make authenticated API requests to the Authifi from browser-based applications.

#### Adding Allowed Origins

- Enter the full origin URL (scheme + host only, no path)
    - **Valid**: `https://myapp.example.com`
    - **Invalid**: `https://myapp.example.com/path`, `myapp.example.com` (missing scheme)

- **Global checkbox** (Super Admins Only):
    - Mark origin as global (applies to all tenants)
    - **Restriction**: Only super administrators can create global origins

- Click **Add** to save

#### Origin Validation Rules

The system enforces strict origin validation:

- Must include scheme (https:// or http://)
- Must not include path, query parameters, or hash fragments
- Must not include credentials (username:password@host)
- Must not have leading/trailing whitespace
- Must not duplicate existing origins

#### Managing Existing Origins

- **View origins**: Table displays all allowed origins for the tenant (includes tenant-specific and global origins)
- **Delete origins**: Click the delete icon to remove (config-defined origins cannot be deleted via UI)
- **Global column**: Indicates whether an origin is global or tenant-specific

#### Advanced Features (Super Admins Only)

- **Allow-list Enabled** toggle
  - When disabled, CORS allow-list enforcement is bypassed (any origin allowed for non-public endpoints)
  - **Security warning**: Only disable temporarily for troubleshooting
  - Default: Enabled

- **Discovery Mode** toggle
  - Logs all incoming origins to help identify what needs whitelisting
  - Only works when allow-list is disabled
  - **Use case**: Transition from permissive to restrictive CORS policy

- **Refresh button**: Clears the server-side CORS cache to apply changes immediately

#### Security Best Practices

- **Only add origins you control and trust**
- **Use HTTPS origins** (not HTTP) for production applications
- **Avoid wildcards** - list each origin explicitly
- **Remove unused origins** to minimize attack surface
- **Monitor the Audit Log** for origin changes (critical security event)
- **Never disable allow-list enforcement** in production unless absolutely necessary and time-limited

---

### User Alert Settings Tab

Configure which login events trigger user email notifications.

**What you can configure**:

- Select which login event types should generate email alerts to users
- Available event types include:
  - Login success
  - Login failure
  - MFA challenge events
  - Password reset events
  - And other authentication-related events

**Use cases**:

- Alert users about suspicious login attempts
- Notify users of successful logins from new devices
- Compliance requirements for security event notifications

**Best practice**: Balance security notifications with alert fatigue; focus on high-risk events (failed logins, new device logins).

---

### Email Settings Tab

Configure SMTP settings for tenant-specific email.

**Availability**: **Tenant admin or super admin only** (tab is hidden for non-admin users).

**Note**: The default tenant uses system-wide email settings and cannot be overridden.

#### Default vs. Custom Email Settings

- **Use Default Email Settings** toggle
  - When enabled: Inherits email configuration from the default tenant
  - When disabled: Allows custom SMTP configuration
  - **Recommendation**: Use default settings unless you have specific requirements

#### Custom SMTP Configuration

Available when "Use Default Email Settings" is disabled:

- **Host**
  - SMTP server hostname
  - Example: `smtp.example.com`

- **Port**
  - SMTP server port
  - Common: 587 (TLS), 465 (SSL), 25 (unencrypted - not recommended)

- **Secure**
  - Enable SSL/TLS connection
  - **Recommendation**: Always enable for production

- **Require TLS**
  - Enforce TLS for connection (STARTTLS)
  - **Security recommendation**: Enable in production environments

- **User**
  - SMTP authentication username
  - Must not contain whitespace

- **Password**
  - SMTP authentication password
  - Must not contain whitespace
  - **Security note**: Stored encrypted; never displayed after saving

#### Email Appearance

- **From**
  - Sender email address for tenant emails
  - Must be a valid email format
  - Leave blank to use the default sender address

#### Testing Email Configuration

- **Test Email Address**
  - Enter a valid email address to test your SMTP configuration
  - Click **Send Test Email** to verify settings
  - Success confirmation shown inline

**Best practices**:

- Test email settings after any changes
- Use a dedicated sending address (e.g., `noreply@yourdomain.com`)
- Ensure SPF/DKIM records are configured for your sending domain to avoid spam filtering

---

### Tenant SAML Certificates Tab

Manage X.509 certificates used for SAML authentication.

**What you can do**:

- **View certificates**: See all uploaded X.509 certificates for your tenant
- **Upload certificates**: Add new certificates for SAML signing/encryption
- **Set active certificate**: Designate which certificate is currently in use
- **Update friendly names**: Label certificates for easy identification
- **Delete unused certificates**: Remove expired or unused certificates

**Security requirements**:

- **Updating certificate names**: Requires tenant admin or super admin
- Certificates are used to establish trust with SAML identity providers

**Certificate lifecycle**:

- Upload new certificate before the old one expires
- Test with new certificate
- Set new certificate as active
- Monitor for issues
- Remove old certificate only after confirming stability

**Security best practices**:

- **Monitor certificate expiration** (check Expiring Items dashboard)
- **Rotate certificates** before expiration (recommend 30-60 days before)
- **Use strong key sizes** (minimum 2048-bit RSA, prefer 4096-bit or ECC)
- **Protect private keys** - never share or expose them

---

### Tenant JWKS Key Set Tab

Manage JSON Web Key Sets (JWKS) used for JWT signing and verification.

**What you can do**:

- **View current JWK**: See the active JSON Web Key and any revoked keys
- **Rotate JWK**: Generate a new JWK while keeping the old one valid (smooth transition)
- **Rotate and Revoke JWK**: Generate a new JWK and immediately revoke the old one
- **Revoke specific JWK**: Revoke a key by its Key ID (kid)

#### Operations

- **Rotate JWK** (recommended)
    - Generates a new key
    - Previous key remains valid temporarily
    - Allows zero-downtime key rotation
    - **Use case**: Regular key rotation schedule

- **Rotate and Revoke JWK** (immediate revocation)
    - Generates new key
    - Immediately invalidates the old key
    - All tokens signed with old key become invalid
    - **Use case**: Security incident or suspected key compromise

- **Revoke JWK** (by kid)
    - Revokes a specific key
    - **Use case**: Targeted key invalidation

**Security best practices**:

- **Rotate JWKs regularly** (every 90-180 days for routine rotation)
- **Use "Rotate" not "Rotate and Revoke"** for scheduled rotations to avoid service disruption
- **Document key rotation** in audit trail
- **Use "Rotate and Revoke" only for emergencies** (key compromise, security incident)
- **Monitor revoked keys list** to ensure old keys are eventually cleaned up

---

### Cookie Consent Banner Tab

Configure cookie consent banners for GDPR and privacy requirements.

**Compliance context**: Required for GDPR, ePrivacy Directive, and similar privacy regulations.

#### Cookie Consent Configuration

- **Enable Cookie Consent Message**
  - Shows/hides the cookie consent banner on login pages
  - **Compliance note**: Required for EU users and recommended globally

- **Cookie Consent Expiration** (days)
  - How long user consent is remembered
  - Default: 150 days
  - After expiration, users must accept again

- **Cookie Consent Message**
  - Text displayed in the consent banner
  - Supports rich text formatting (via Summernote editor)
  - Default: "We use cookies to ensure that you have the best experience on our website. If you continue to use this site we assume that you accept this."
  - **Legal recommendation**: Include link to privacy policy

#### Banner Styling

- **Cookie Consent Banner Color**
  - **Background**: Banner background color (default: `#2B373B`)
  - **Text**: Banner text color (default: `#FFFFFF`)
  - Live preview shows actual appearance

- **Cookie Consent Button Color**
  - **Background**: Accept button background (default: `#e7db08`)
  - **Text**: Accept button text color (default: `#000000`)
  - Live preview shows button appearance

- **Cookie Consent Button Text**
  - Text on the accept button
  - Default: `Accept`
  - Keep short and clear (e.g., "Accept", "OK", "I Understand")

**Best practices**:

- **Ensure text is readable** - verify color contrast meets accessibility standards
- **Keep message concise** - users should understand quickly
- **Include privacy policy link** in the consent message
- **Set appropriate expiration** - balance user experience with regulatory requirements (typically 6-12 months)

---

### Contacts Tab

Manage contact information associated with the tenant.

**Availability**: View-only for regular users; **editing requires tenant admin or super admin**.

**What you can do**:

- **Add contacts**: Create new contact records for the tenant
- **Assign contact types**: Categorize contacts (e.g., technical contact, security contact, billing contact)
- **Update contact details**: Modify name, email, phone number
- **Remove contacts**: Delete obsolete contact records

**Contact fields**:

- **Name**: Contact person's full name
- **Email**: Contact email address (validated format)
- **Phone Number**: Contact phone number
- **Contact Type**: Role or purpose of this contact

**Use cases**:

- Emergency security contacts
- Technical escalation contacts
- Service notifications
- Compliance and auditing

**Best practice**: Keep contact information current; designate at least one technical and one security contact per tenant.

---

## Security Recommendations

### Critical Security Controls

- **Always enable Admin MFA**
    - Protects against credential theft
    - Required for compliance in most frameworks

- **Use short session lifetimes**
    - Recommend: 1 hour maximum session, 20 minutes idle timeout
    - Shorter for high-security tenants

- **Restrict allowed origins**
    - Only add origins you control
    - Use HTTPS (never HTTP in production)
    - Remove unused origins immediately

- **Monitor admin actions**
    - Review audit logs regularly for admin activity
    - Set up alerts for sensitive operations (certificate changes, admin group modifications)

- **Rotate cryptographic keys**
    - SAML certificates: Before expiration (30-60 days notice)
    - JWKS: Every 90-180 days for routine rotation
    - Immediately upon suspected compromise

- **Use strong authentication**
    - Require MFA for admin accounts
    - Use trusted identity providers (flagged as `isTrusted`)
    - Avoid password-only authentication

### Compliance Considerations

- **Audit log retention**: Configure based on compliance requirements (SOC 2, HIPAA, etc.)
- **Cookie consent**: Enable for GDPR/ePrivacy compliance
- **Contact information**: Maintain current contacts for incident response
- **Session management**: Configure based on data sensitivity and compliance frameworks

### Regular Maintenance Tasks

**Weekly**:

- Review expiring items dashboard
- Check for unusual authentication patterns in login metrics

**Monthly**:

- Review and clean up unused allowed origins
- Verify contact information is current
- Review tenant admin membership

**Quarterly**:

- Audit admin access logs for suspicious activity
- Review and update security settings based on threat landscape
- Test disaster recovery procedures (certificate rotation, admin account recovery)

**Annually**:

- Review and update all branding and communications for accuracy
- Audit the tenant's security configuration
- Review and update cookie consent messaging for regulatory changes

---

## Additional Resources

- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)
- **Authifi API Documentation**: Available via Help > API Documentation in the UI

---

## Getting Help

If you encounter issues or need assistance:

- **Check audit logs** for error details
- **Review the Usage Report** to identify misconfigurations
- **Contact your super administrator** for elevated privilege requests
- **Consult API documentation** for advanced configurations

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators

# Resources and Tools Guide

This guide explains how tenant administrators manage templates, images, secrets, and scheduled jobs through the Authifi UI.

## Table of Contents

- [Overview](#overview)
- [Resources and Tools Menu Options](#resources-and-tools-menu-options)
  - [Email Templates](#email-templates)
  - [HTML Templates](#html-templates)
  - [Images](#images)
  - [Secrets](#secrets)
  - [Jobs](#jobs)
- [Email Template Configuration](#email-template-configuration)
- [HTML Template Configuration](#html-template-configuration)
- [Secret Management Best Practices](#secret-management-best-practices)
- [Job Configuration](#job-configuration)

---

## Overview

The **Resources and Tools** section lets you manage reusable resources:

- **Templates**: Customize email and HTML templates for branding and user experience
- **Images**: Centralized image storage with CDN delivery
- **Secrets**: Secure storage for API keys, credentials, and configuration values
- **Jobs**: Automated maintenance tasks and scheduled operations

**Key capabilities**:

- Consistent branding across all user communications
- Secure credential management with encryption
- Automated system maintenance and cleanup
- Centralized resource sharing across applications

---

## Resources and Tools Menu Options

### Email Templates

**Location**: Resources and Tools > Email Templates

**Purpose**: Customize email communications sent by the Authifi system (invitations, password resets, notifications, etc.).

#### What You Can Do

- **View templates**: See all system and custom email templates
- **Create templates**: Build custom email templates for specific use cases
- **Edit templates**: Modify existing custom templates
- **Import templates**: Import template definitions from JSON
- **Delete/Reset templates**: Remove custom templates or reset system templates to default

#### Template Types

**System templates** (read-only):

- Password reset emails
- MFA enrollment instructions
- Group invitations
- Access request notifications
- Welcome emails
- TOTP reset emails
- System notifications

**Custom templates**:

- Override system templates with tenant-specific branding
- Create new templates for custom workflows
- Modify subject lines, content, and formatting
- Add custom variables and logic

#### Email Templates Dashboard

**Table columns**:

- **Name**: Template identifier
- **System/Custom**: Origin (System templates are read-only, Custom can be edited)
- **UpdatedAt**: Last modification timestamp
- **Category**: Template purpose (e.g., "Password Reset", "Group Invitation")
- **Details**: Edit template (Settings icon)
- **Delete**: Remove custom template or reset system template

**Actions**:

- **+ NEW EMAIL TEMPLATE**: Create custom template
- **Import**: Import template from JSON file

#### Creating an Email Template

- Click **+ NEW EMAIL TEMPLATE**
- Fill in template details:
    - **Template Category\***: Purpose of email (dropdown)
        - Begin From System Default Template: Copy system template as starting point
    - **Name\***: Unique identifier (e.g., "Custom Password Reset")
    - **Description\***: Template purpose
    - **Email From\***: Sender address (can use variables)
    - **CC**: Carbon copy recipients (optional)
    - **BCC**: Blind carbon copy recipients (optional)
    - **Email Subject\***: Subject line (supports variables)
    - **Email Body\***: HTML content (Handlebars template)
- **Preview**: Test template rendering with sample data
- Click **Save**

#### Template Variables

**Available variables** (Handlebars syntax):

- `{{user.email}}`: Recipient email
- `{{user.name}}`: User's display name
- `{{tenant.name}}`: Tenant name
- `{{link}}`: Action link (password reset, invitation, etc.)
- `{{expirationTime}}`: Link expiration time
- `{{appName}}`: Application name

**Example template**:

```handlebars
Hello
{{user.name}}, Your password reset link for
{{tenant.name}}:
{{link}}

This link expires in
{{expirationTime}}. If you didn't request this, please ignore this email.
```

#### System vs. Custom Templates

**System templates**:

- Cannot be deleted
- Can be overridden with custom version
- Automatically update with system upgrades
- Displayed with "System" origin

**Custom templates**:

- Full control over content
- Can be deleted
- Persists across system upgrades
- Displayed with "Custom" origin

**Overriding system templates**:

- Create new template with same category as system template
- Custom version takes precedence
- To revert: Delete custom template

#### Importing Templates

**Import format** (JSON):

```json
{
  "name": "Custom Welcome Email",
  "categoryLabel": "welcome-email.hbs",
  "description": "Welcome email for new users",
  "messageFrom": "noreply@example.com",
  "subject": "Welcome to {{tenant.name}}",
  "body": "<html>...</html>"
}
```

**Import process**:

- Click **Import** button
- Select JSON file
- Template loads into editor
- Review and modify as needed
- Save to create template

---

### HTML Templates

**Location**: Resources and Tools > HTML Templates

**Purpose**: Customize HTML pages displayed during authentication flows (login pages, confirmation pages, error pages).

#### What You Can Do

- **View templates**: See all system and custom HTML templates
- **Create templates**: Build custom HTML pages
- **Edit templates**: Modify existing templates with Monaco code editor
- **View history**: See previous versions of templates
- **Preview templates**: Test rendering before deploying
- **Delete/Reset templates**: Remove custom templates

#### Template Types

**Confirmation pages**:

- Group invitation confirmation
- Access request confirmation
- Email verification
- Account activation

**Custom pages**:

- Custom login pages
- Custom landing pages
- Branded error pages
- Custom consent pages

#### HTML Templates Dashboard

**Table columns**:

- **Name**: Template identifier
- **System/Custom**: Origin
- **Type**: Template purpose
- **Description**: Template description
- **UpdatedAt**: Last modification
- **Details**: Edit template (Settings icon)
- **Delete**: Remove custom template

**Actions**:

- **+ NEW HTML TEMPLATE**: Create new template

#### Creating an HTML Template

- Click **+ NEW HTML TEMPLATE**
- Fill in template details:
    - **Name\***: Unique identifier
    - **Type\***: Template purpose (dropdown)
        - Login page
        - Landing page
        - Confirmation page
        - Error page
        - Begin From System Default Template: Copy system template
    - **Description\***: Template purpose
    - **HTML Template\***: Full HTML page (Monaco editor)
- **Preview**: Test rendering in new tab
- **History**: View previous versions (for existing templates)
- Click **Save**

#### Template Structure

**Basic structure**:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{pageTitle}}</title>
    <style>
      /* Custom CSS */
    </style>
  </head>
  <body>
    <div class="container">
      <h1>{{heading}}</h1>
      <p>{{message}}</p>
      {{#if showButton}}
      <a href="{{buttonLink}}" class="button">{{buttonText}}</a>
      {{/if}}
    </div>
    <script>
      // Custom JavaScript
    </script>
  </body>
</html>
```

#### Template Variables

**Common variables**:

- `{{tenant.name}}`: Tenant display name
- `{{applicationName}}`: Application name
- `{{userName}}`: User's name
- `{{redirectUrl}}`: Where to redirect after action
- `{{errorMessage}}`: Error message (for error pages)

#### Template History

**Version tracking**:

- Every template save creates a new version
- Click **History** button to view all versions
- Each version shows timestamp or version number
- Current version is tagged
- Click any version to view its content
- Cannot revert to old versions (must manually copy content)

**Use case**: Audit template changes or recover accidentally deleted content.

---

### Images

**Location**: Resources and Tools > Images

**Purpose**: Centralized image storage and management for use in login pages, emails, and branding.

#### What You Can Do

- **Upload images**: Add images to tenant storage
- **Edit metadata**: Update name, description, alt text
- **Copy public URL**: Get CDN URL for use in templates
- **View dimensions**: See image width and height
- **Delete images**: Remove unused images

#### Image Management Dashboard

**Table columns**:

- **Name**: Image filename or custom name
- **Description**: Purpose or context
- **Alt Text**: Accessibility text
- **Public URL**: CDN URL (click to copy)
- **Dimensions**: Width × Height (e.g., 1920 × 1080)
- **Updated**: Last modification timestamp
- **Edit**: Update metadata (Edit icon)
- **Delete**: Remove image (Delete icon)

**Actions**:

- **+ UPLOAD IMAGE**: Add new image

#### Uploading an Image

- Click **+ UPLOAD IMAGE**
- Fill in image details:
    - **Image Name\***: Descriptive name
    - **Description**: Purpose or context
    - **Alt Text\***: Accessibility description (for screen readers)
    - **File Upload\***: Select image file
- Click **Save**
- Image is uploaded to CDN
- Public URL is generated

**Supported formats**:

- PNG (.png)
- JPEG (.jpg, .jpeg)
- GIF (.gif)
- WebP (.webp)
- SVG (.svg)

**File size limits**:

- Recommended: < 2MB per image
- Maximum: Check with super administrator

#### Using Images in Templates

**Copy public URL**:

- Find image in dashboard
- Click **Public URL** (copy icon)
- URL copied to clipboard

**In email templates**:

```html
<img
  src="https://cdn.example.com/tenants/123/images/logo.png"
  alt="Company Logo"
  width="200"
/>
```

**In HTML templates**:

```html
<div class="header">
  <img src="{{logoUrl}}" alt="{{tenant.name}} Logo" />
</div>
```

**In tenant settings**:

- Login page background images
- Email header logos
- Tenant icons
- Landing page banners

#### Image Best Practices

- **Optimization**:
    - Compress images before upload
    - Use appropriate format (PNG for logos, JPEG for photos)
    - Specify dimensions to prevent layout shifts

- **Accessibility**:
    - Always provide meaningful alt text
    - Use descriptive image names
    - Test with screen readers

- **Organization**:
    - Use consistent naming conventions
    - Add descriptive text for context
    - Delete unused images regularly

- **Performance**:
    - Images are served from CDN (fast delivery)
    - Use responsive images for different screen sizes
    - Consider lazy loading for large images

---

### Secrets

**Location**: Resources and Tools > Secrets

**Purpose**: Secure storage for API keys, passwords, tokens, and other sensitive configuration values.

#### What You Can Do

- **Create secrets**: Store sensitive values securely
- **Rotate key**: Generate new encryption key
- **Edit secrets**: Update secret values (write-only for sensitive secrets)
- **View secrets**: Access non-sensitive configuration variables
- **Delete secrets**: Remove unused secrets
- **Share secrets**: Make secrets available to all tenants (super admin only)

#### Secrets Dashboard

**Table columns**:

- **Name**: Secret identifier
- **Description**: Secret purpose
- **Secret Provider Type**: Storage backend (Local, AWS Secrets Manager, etc.)
- **System Shared**: Available to all tenants (system admin feature)
- **Owned**: Whether this tenant owns the secret
- **Created At**: Creation timestamp
- **Last Modified**: Last update timestamp
- **Expires**: Expiration (days remaining or "never")
- **Secret Type**: Sensitive (write-only) or Variable (readable)
- **Edit**: Update secret (Settings icon)
- **Delete**: Remove secret (Delete icon)

**Actions**:

- **Rotate Key**: Generate new encryption key (super admin)
- **+ NEW Secret**: Create new secret

#### Creating a Secret

- Click **+ NEW Secret**
- Fill in secret details:
    - **Name\***: Unique identifier (e.g., "smtp_password", "api_key")
    - **Username\***: Associated username or identifier
    - **Description\***: Secret purpose
    - **Secret\***: Actual secret value (encrypted at rest)
    - **Secret Provider Type\***: Storage backend (default: Local)
    - **Expiration Date**: Optional expiration (recommended)
    - **Secret Write Only**: Check to make secret write-only (recommended for sensitive data)
    - **Secret Type**: Sensitive (hidden) or Variable (readable)
- **System Shared** (super admin only): Make secret available to all tenants
- Click **Save**

**Secret is encrypted** using tenant-specific key and stored securely.

#### Secret Types

**Sensitive secrets** (recommended for credentials):

- Write-only (cannot read back after creation)
- Encrypted at rest
- Cannot be viewed in UI
- Used for passwords, API keys, tokens
- **Security**: Prevents secret exposure via UI

**Variable secrets** (for non-sensitive config):

- Readable after creation
- Still encrypted at rest
- Can be viewed and copied in UI
- Used for URLs, configuration values, non-sensitive settings

**Best practice**: Always use Sensitive type for passwords, tokens, and keys.

#### Secret Expiration

**Why set expiration**:

- Force periodic secret rotation
- Reduce exposure window for compromised secrets
- Compliance requirement (some standards require 90-day rotation)

**Expiration behavior**:

- Secret shows "X days" until expiration
- Expired secrets show "Expired"
- Expired secrets still work but should be rotated
- No automatic deletion (manual cleanup required)

**Recommended expiration periods**:

- API keys: 90-180 days
- Passwords: 30-90 days
- Service account tokens: 180-365 days
- Long-lived secrets: Never (only if absolutely required)

#### Using Secrets in Jobs

**Reference secrets in job scripts**:

```javascript
// Access secret value
const apiKey = secrets['api_key'];
const dbPassword = secrets['db_password'];

// Use in HTTP request
const response = await fetch(apiUrl, {
  headers: {
    Authorization: `Bearer ${apiKey}`
  }
});
```

**Secrets are injected** into job execution context automatically.

#### Key Rotation

**What is key rotation**:

- Generates new encryption key for tenant
- Re-encrypts all secrets with new key
- Old key is securely destroyed

**When to rotate**:

- Suspected key compromise
- Periodic security maintenance (annually)
- After administrator departure
- Compliance requirement

**How to rotate** (super admin only):

- Click **Rotate Key** button
- Confirm rotation
- System re-encrypts all secrets
- Applications continue working (no changes needed)

**Warning**: Rotation cannot be undone.

---

### Jobs

**Location**: Resources and Tools > Jobs

**Purpose**: Schedule automated maintenance tasks, data processing, and system cleanup operations.

#### What You Can Do

- **View jobs**: See all configured scheduled jobs
- **Create jobs**: Define new automated tasks
- **Edit jobs**: Modify job configuration
- **View history**: See job execution logs and results
- **Delete jobs**: Remove unused jobs
- **Enable/Disable**: Control job execution

**Super admin only**: Create and edit jobs (requires elevated permissions).

#### Jobs Dashboard

**Table columns**:

- **Name**: Job identifier
- **Description**: Job purpose
- **UpdatedAt**: Last configuration change
- **LatestStatus**: Most recent execution result (success, failed, N/A)
- **Details**: View execution history (Info icon)
- **Settings**: Edit job configuration (Settings icon)
- **Delete**: Remove job (Delete icon)
- **Enabled**: Whether job is active

**Actions**:

- **+ NEW JOB**: Create new scheduled job (super admin only)

#### Job Types

**Maintenance jobs**:

- **Trim Job**: Automatic log cleanup based on retention policies
- **Expiration Check**: Monitor and notify about expiring certificates, secrets
- **Archive Job**: Archive old audit logs to external storage
- **Cleanup Job**: Remove expired tokens, sessions, grants

**Data processing jobs**:

- **Generic Job**: Custom JavaScript job for any task
- **Batch Invite Users**: Bulk user invitation from script
- **Tenant JWK Expire**: Notify about expiring tenant signing keys
- **Image Malware Check**: Scan uploaded images for malware

**Notification jobs**:

- **Expiring User Profiles**: Notify about users nearing expiration
- **Expiring Tenant User Profiles**: Tenant-specific user expiration alerts
- **Adding Tenants Latest Login**: Update tenant with latest login times

#### Creating a Job

**System admin required**: Only super admins can create and edit jobs.

- Click **+ NEW JOB**
- Fill in job details:
    - **Job Name\***: Unique identifier
    - **Job Description**: Purpose and context
    - **Job Type\***: Select from dropdown
    - **Cron Schedule\***: When job runs (cron expression)
    - **Email Recipients**: Notification recipients
    - **Recipient Type**: Email or Group
    - **Email Template**: Template for notifications
    - **Custom Script**: JavaScript for Generic Job type
    - **Job-Specific Options**: Varies by job type
- **Enable Job**: Check to activate immediately
- Click **Save**

#### Cron Schedule

**Format**: Standard cron expression (5 fields)

```
* * * * *
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, Sunday = 0 or 7)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

**Examples**:

- `0 2 * * *`: Daily at 2:00 AM
- `0 2 * * 0`: Weekly on Sunday at 2:00 AM
- `0 */4 * * *`: Every 4 hours
- `30 1 1 * *`: Monthly on 1st at 1:30 AM
- `0 3 * * 1-5`: Weekdays at 3:00 AM

**Cron helper**: UI displays human-readable description ("At 02:00 AM").

#### Generic Job (Custom Script)

**Purpose**: Execute custom JavaScript for any automated task.

**Script template**:

```javascript
const { logger, secrets, config } = context;

// Access configuration
const apiUrl = config.apiUrl;
const apiKey = secrets['api_key'];

// Log output
logger.info('Job started');

try {
  // Your custom logic here
  const data = await fetchData(apiUrl, apiKey);
  await processData(data);

  logger.info('Job completed successfully');
  return { success: true, recordsProcessed: data.length };
} catch (error) {
  logger.error('Job failed', error);
  throw error;
}
```

**Available context**:

- `logger`: Logging functions (info, warn, error)
- `secrets`: Access to secret values
- `config`: Job configuration values
- `fetch`: HTTP client for API calls
- `tenantId`: Current tenant ID

#### Trim Job

**Purpose**: Automatic cleanup of old audit logs, event logs, notification logs.

**Configuration**:

- **Age Limit**: Delete logs older than X days
- **Check Number**: How many logs to check per run
- **Trim Ratio**: Percentage of old logs to delete per run

**Example**:

```
Age Limit: 365 days
Check Number: 10000
Trim Ratio: 0.5 (50%)

Result: Check 10,000 oldest logs, delete those older than 365 days (up to 50% of checked logs per run)
```

**Use case**: Maintain database size and comply with retention policies.

#### Batch Invite Users

**Purpose**: Bulk invite users to groups from JavaScript array.

**Configuration**:

- **Group**: Target group for invitations
- **Custom Script**: JavaScript returning array of users
- **Email Template**: Invitation email template
- **Redirect URL**: Where users land after accepting

**Script template**:

```javascript
// Return array of user objects
return [
  { email: 'user1@example.com', identityIssuerId: 123 },
  { email: 'user2@example.com', identityIssuerId: 123 },
  { email: 'user3@example.com', identityIssuerId: 123 }
];
```

**Use case**: Automated onboarding based on external data source.

#### Job Execution History

**View job history**:

- Click **Details** (info icon) on job
- See all historical executions:
    - Timestamp
    - Status (success, failed)
    - Duration
    - Output logs
    - Error messages (if failed)
- Filter by status or date range

**Troubleshooting failed jobs**:

- Review error message in history
- Check job logs for stack trace
- Verify job configuration
- Test custom script in isolation
- Check secret availability

---

## Email Template Configuration

### Template Category Reference

**Password Reset** (`password-reset.hbs`):

- Sent when user requests password reset
- Variables: `{{link}}`, `{{expirationTime}}`

**MFA Enrollment** (`mfa-enrollment.hbs`):

- Sent when user enrolls in MFA
- Variables: `{{qrCode}}`, `{{secret}}`

**Group Invitation** (`group-invitation.hbs`):

- Sent when user is invited to group
- Variables: `{{groupName}}`, `{{inviterName}}`, `{{link}}`

**Access Request** (`access-request.hbs`):

- Sent to approvers for pending requests
- Variables: `{{requesterName}}`, `{{resourceName}}`, `{{link}}`

**TOTP Reset** (`totp-reset.hbs`):

- Sent when TOTP device is reset
- Variables: `{{link}}`, `{{expirationTime}}`

**System Notification** (`system-notification.hbs`):

- Sent for admin announcements
- Variables: `{{title}}`, `{{message}}`, `{{link}}`

### Email Branding

**Tenant email branding** (configured in Tenant Settings > Branding):

- Header background color
- Header text color
- Body text color
- Font family and size
- Email header title
- Email template logo

**Email templates inherit branding** automatically from tenant settings.

### Template Testing

**Preview template**:

- Click **Preview** button in template editor
- Sample data is injected
- Template renders in new tab/window
- Review appearance and formatting
- Close preview and return to editor

**Test email sending**:

- Save template
- Trigger action that uses template (e.g., request password reset)
- Check email delivery
- Verify rendering in different email clients
- Iterate as needed

---

## HTML Template Configuration

### Template Types Reference

**Login Page** (`login-page`):

- Custom branded login experience
- Replaces default login page
- Full control over HTML/CSS/JavaScript
- Variables: `{{tenant.name}}`, `{{idpButtons}}`

**Landing Page** (`landing-page`):

- Page shown after successful login
- Can include welcome message, instructions
- Variables: `{{user.name}}`, `{{applicationName}}`

**Confirmation Page** (`confirmation-page`):

- Shown after user accepts invitation
- Configurable success message
- Variables: `{{groupName}}`, `{{redirectUrl}}`

**Error Page** (`error-page`):

- Custom error pages for auth failures
- Better user experience than default errors
- Variables: `{{errorCode}}`, `{{errorMessage}}`

### Template Styling

**CSS considerations**:

- Mobile responsive design (use viewport meta tag)
- Cross-browser compatibility
- Accessibility (WCAG 2.1 AA compliance)
- Consistent with tenant branding

**Example responsive CSS**:

```css
.container {
  max-width: 600px;
  margin: 0 auto;
  padding: 20px;
}

@media (max-width: 768px) {
  .container {
    padding: 10px;
  }

  h1 {
    font-size: 1.5rem;
  }
}
```

### JavaScript in Templates

**Allowed functionality**:

- Form validation
- Dynamic content rendering
- Animations and interactions
- Analytics tracking

**Security restrictions**:

- Cannot access sensitive Authifi APIs
- Cannot modify authentication flow
- Sandboxed execution environment
- XSS protection enabled

**Example**:

```javascript
document.addEventListener('DOMContentLoaded', function () {
  // Form validation
  const form = document.querySelector('form');
  form.addEventListener('submit', function (e) {
    const email = form.querySelector('[name="email"]').value;
    if (!email.includes('@')) {
      e.preventDefault();
      alert('Please enter a valid email address');
    }
  });
});
```

---

## Secret Management Best Practices

### Security Recommendations

- **Use Sensitive type for all credentials**:
    - Passwords: Always Sensitive
    - API keys: Always Sensitive
    - Tokens: Always Sensitive
    - Only use Variable for non-sensitive config

- **Set expiration dates**:
    - Force rotation every 90-180 days
    - Shorter expiration for high-privilege secrets
    - Set calendar reminders for expiration

- **Least privilege**:
    - Create separate secrets for each purpose
    - Don't reuse secrets across systems
    - Limit secret access to necessary jobs

- **Rotation strategy**:
    - Rotate immediately if compromised
    - Periodic rotation (quarterly minimum)
    - Document rotation procedures
    - Test applications after rotation

- **Audit secret access**:
    - Review audit logs for secret reads
    - Monitor for unusual access patterns
    - Alert on secret modifications
    - Track secret usage in jobs

### Common Use Cases

**SMTP credentials**:

```
Name: smtp_password
Username: noreply@example.com
Description: SMTP password for outbound email
Secret: [actual password]
Type: Sensitive
Expiration: 90 days
```

**External API key**:

```
Name: crm_api_key
Username: api_integration
Description: API key for CRM integration
Secret: [actual key]
Type: Sensitive
Expiration: 180 days
```

**Database password**:

```
Name: reporting_db_password
Username: reporting_user
Description: Read-only reporting database password
Secret: [actual password]
Type: Sensitive
Expiration: 90 days
```

**Non-sensitive config**:

```
Name: api_base_url
Username: config
Description: Base URL for external API
Secret: https://api.example.com/v1
Type: Variable
Expiration: Never
```

---

## Job Configuration

### Job Design Best Practices

- **Idempotency**:
    - Jobs should be safe to run multiple times
    - Check for existing state before creating resources
    - Use atomic operations where possible

- **Error handling**:
    - Wrap operations in try/catch
    - Log errors with context
    - Return meaningful error messages
    - Don't swallow exceptions

- **Performance**:
    - Process data in batches
    - Set reasonable limits (don't process millions of records)
    - Use pagination for large datasets
    - Add timeouts for external calls

- **Logging**:
    - Log job start and completion
    - Log key milestones and metrics
    - Include record counts processed
    - Avoid logging sensitive data

- **Notifications**:
    - Send success notification for critical jobs
    - Always send failure notifications
    - Include error details
    - Set appropriate recipient groups

### Example Generic Job

**Purpose**: Sync users from external HR system to Authifi tenant.

```javascript
const { logger, secrets, config } = context;

// Configuration
const hrApiUrl = config.hrApiUrl || 'https://hr.example.com/api';
const syncGroup = config.syncGroup || 'employees';
const apiKey = secrets['hr_api_key'];

logger.info(`Starting HR sync for group: ${syncGroup}`);

try {
  // Fetch users from HR system
  const response = await fetch(`${hrApiUrl}/users`, {
    headers: { Authorization: `Bearer ${apiKey}` }
  });

  if (!response.ok) {
    throw new Error(`HR API error: ${response.status}`);
  }

  const hrUsers = await response.json();
  logger.info(`Fetched ${hrUsers.length} users from HR system`);

  // Get existing Authifi users
  const authUsersResponse = await fetch(
    `${config.authApiUrl}/tenants/${tenantId}/users`,
    { headers: { Authorization: `Bearer ${config.authToken}` } }
  );
  const authUsers = await authUsersResponse.json();
  const authEmailsSet = new Set(authUsers.map((u) => u.email));

  // Find new users to create
  const newUsers = hrUsers.filter((u) => !authEmailsSet.has(u.email));
  logger.info(`Found ${newUsers.length} new users to create`);

  // Create new users
  let created = 0;
  let failed = 0;

  for (const user of newUsers) {
    try {
      await createAuthUser(user, tenantId, syncGroup, config.authToken);
      created++;
    } catch (error) {
      logger.error(`Failed to create user ${user.email}:`, error.message);
      failed++;
    }
  }

  logger.info(`Sync complete: ${created} created, ${failed} failed`);

  return {
    success: true,
    usersProcessed: hrUsers.length,
    usersCreated: created,
    usersFailed: failed
  };
} catch (error) {
  logger.error('HR sync failed:', error);
  throw error;
}

// Helper function
async function createAuthUser(hrUser, tenantId, groupName, authToken) {
  const response = await fetch(
    `${config.authApiUrl}/tenants/${tenantId}/users`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${authToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        email: hrUser.email,
        name: hrUser.name,
        identityIssuerId: config.defaultIdpId
      })
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to create user: ${response.status}`);
  }

  // Add to group
  const user = await response.json();
  await addToGroup(user.id, groupName, tenantId, authToken);
}
```

### Monitoring Jobs

**Check job health**:

- Navigate to Resources and Tools > Jobs
- Review **LatestStatus** column
- Look for "failed" status
- Click **Details** to investigate failures

**Common failure reasons**:

- External API unavailable
- Secret expired or invalid
- Configuration error in script
- Network timeout
- Permission denied

**Remediation steps**:

- Review job execution logs
- Check error message and stack trace
- Verify external dependencies (APIs, databases)
- Test secret validity
- Update job configuration
- Enable job and wait for next run

---

## Additional Resources

- [Tenant Administrator Guide](tenant-admin-guide.md)
- [SSO Integration Guide](sso-integration-guide.md)
- [Monitoring Guide](monitoring-guide.md)
- [Access Requests Guide](access-requests-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)

---

## Getting Help

For assistance with resources and tools:

- **Template issues**: Check syntax, preview before deploying, test with sample data
- **Secret problems**: Verify expiration, check secret type, test in job execution
- **Job failures**: Review execution logs, check Job Logs dashboard, verify dependencies
- **Image upload**: Check file size, verify format, ensure permissions
- **Contact admin team**: For system-level issues or elevated permission requests

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators, Super Administrators  
**Scope**: Template management, resource storage, secrets, scheduled jobs

# Monitoring and Logging Guide

Tenant administrators use the Authifi UI to monitor authentication activity, review audit trails, and investigate system behavior.

## Table of Contents

- [Overview](#overview)
- [Monitoring Menu Options](#monitoring-menu-options)
  - [Audit Logs](#audit-logs)
  - [Event Logs](#event-logs)
  - [Job Logs](#job-logs)
  - [Notification Logs](#notification-logs)
  - [Sessions](#sessions)
  - [Grants](#grants)
- [Natural Language Queries](#natural-language-queries)
- [Exporting Logs](#exporting-logs)
- [Security and Compliance](#security-and-compliance)

---

## Overview

Use **Monitoring** to review authentication and administrative activity in your tenant:

- **Security monitoring**: Track login attempts, failures, and suspicious activity
- **Compliance and audit**: Audit trail of all administrative actions
- **Operational insights**: Monitor system jobs, email delivery, and active sessions
- **Troubleshooting**: Investigate authentication issues and configuration changes
- **User activity**: Track individual user actions and access patterns

## Monitoring Menu Options

### Audit Logs

**Location**: Monitoring > Audit Logs

Audit logs record administrative actions and configuration changes within the tenant.

#### What is Logged

**Administrative actions**:

- Identity provider changes (create, update, delete, enable/disable)
- Application (client) configuration changes
- User management actions (create, update, delete, role changes)
- Group modifications (membership, roles, permissions)
- Role and permission assignments
- Template changes (HTML, email)
- Tenant settings updates
- Secret and certificate management

**Who, what, when, where**:

- **Action**: Type of change (e.g., "update IdentityProvider", "create User")
- **Target**: Resource that was modified (e.g., "Google IdP", "user@example.com")
- **Actor**: Admin user who performed the action
- **Subject**: Secondary affected entity (optional)
- **ClientActor**: Application through which action was performed
- **Timestamp**: Exact date and time of action
- **Old Entity**: State before change (JSON)
- **New Entity**: State after change (JSON)
- **Diff**: Computed differences between old and new
- **Comments**: Optional admin-provided justification

#### Viewing Audit Logs

**Table columns**:

- **Action**: What was done
- **Target**: What was affected
- **Actor**: Who did it (clickable to view user details)
- **Subject**: Secondary affected resource
- **ClientActor**: Application used
- **Comments**: Admin notes or justification
- **TimeStamp**: When it occurred
- **Old Entity**: Click to copy previous state
- **New Entity**: Click to copy new state
- **Diff**: View computed differences
- **Details**: Open full audit log entry

**Default view**: Last 7 days of audit logs, sorted by most recent.

#### Searching and Filtering Audit Logs

**Quick search**: Use the search bar to find logs by:

- **Action**: Search for specific action types (e.g., "update", "delete")
- **Target**: Find changes to specific resources
- **Actor**: Filter by admin email or username
- **Subject**: Search secondary affected entities
- **Comments**: Find logs with specific justifications

**Advanced search**: Click search icon to specify:

- **Field selection**: Choose which field to search
- **Date range**: Filter by time period
  - Presets: Today, Yesterday, Last 7/30 days, This/Last Month
  - Custom: Select start and end dates
- **Multiple criteria**: Combine multiple search conditions

**Natural language queries**: Enable advanced filtering with plain English:

- "action is 'update IdentityProvider' and the timestamp is from last month"
- "the actor is like example.com"
- "the target is 'test role'"

See [Natural Language Queries](#natural-language-queries) for more examples.

#### Understanding Audit Log Details

**Old Entity vs. New Entity**:

- **Old Entity**: Complete JSON snapshot of resource before change
- **New Entity**: Complete JSON snapshot after change
- **Diff**: Automatically computed differences showing only what changed

**Diff notation**:

```json
{
  "fieldName__deleted": "value that was removed",
  "fieldName__added": "value that was added",
  "fieldName": {
    "_old": "previous value",
    "_new": "current value"
  }
}
```

Use this workflow to investigate "Who changed the Google IdP configuration last week?"

- Search for Target: "Google"
- Set date range: Last 7 days
- Review Actor column to see who made changes
- Click Details to see Old Entity vs. New Entity
- Review Diff to see exactly what changed

#### Exporting Audit Logs

**Super admin only**: Audit log export requires super admin permissions.

**Export options**:

- Click **Export Audit Logs** button
- Choose format:
    - **JSON**: Machine-readable, includes all fields
    - **CSV**: Spreadsheet-compatible, simplified format
- Specify number of logs to export (max 1,000,000)
- File downloads automatically

**Best practices for exports**:

- Export logs regularly for long-term retention
- Store exports securely (contain sensitive information)
- Use JSON format for complete data preservation
- Export before major system changes
- Maintain export archives per compliance requirements

#### Restoring Archived Audit Logs

**Super admin only**: Restore functionality requires super admin permissions.

Restore previously exported audit logs after tenant data cleanup or migration.

**Steps**:

- Click **Restore Archive** button
- Select previously exported JSON file
- Logs are imported back into the audit log database
- Restored logs appear in the audit log table

**Use cases**:

- Restore logs after data retention policy cleanup
- Migrate audit history to new tenant
- Forensic investigation requiring historical data

---

### Event Logs

**Location**: Monitoring > Event Logs

Event logs track authentication events, including login, logout, MFA, and failures.

#### What is Logged

**Login events**:

- **Successful logins**: User authenticated successfully
- **Failed logins**: Invalid credentials, account locked, or configuration error
- **MFA events**: TOTP verification, WebAuthn authentication
- **Password resets**: User-initiated password changes
- **Account lockouts**: Too many failed attempts
- **Session creation**: New authentication session established

**Event details**:

- **Event Type**: login, logout, mfa, password_reset, etc.
- **Client Name**: Application user logged into
- **Provider Name**: Identity provider used (Google, Azure AD, Local, etc.)
- **Email**: User's email address
- **Username**: User's username (if different from email)
- **IP Address**: Source IP of authentication attempt
- **User Agent**: Browser/device information
- **Created On**: Timestamp of event
- **Details**: Full event context (JSON)

#### Viewing Event Logs

**Table columns**:

- **Event Type**: Type of authentication event
- **Client Name**: Application (clickable to view client details)
- **Provider Name**: Identity provider used
- **Created On**: Date and time of event
- **Email**: User email (clickable to view user details)
- **Username**: User's username
- **IP Address**: Source IP
- **User Agent**: Browser/device info
- **Details**: Full event JSON

**Default view**: Last 7 days of events.

#### Searching and Filtering Event Logs

**Quick search**: Search by email (default field).

**Filter options**:

- **Event Type**: Filter by login, logout, mfa, etc.
- **Client Name**: Show events for specific application
- **Provider Name**: Filter by identity provider
- **Email**: Find events for specific user
- **Username**: Search by username
- **IP Address**: Filter by source IP
- **Date range**: Time period selection

**Natural language queries**:

- "the event type is login and the log was created in the last 7 days"
- "client is reportmgr and the log was created one week ago"
- "provider name is google and email contains @example.com"

#### Security Monitoring with Event Logs

**Failed login detection**:

- Filter by Event Type: "failed"
- Set date range: Last 24 hours
- Look for patterns:
    - Multiple failures for same email (brute force attempt)
    - Multiple failures from same IP (distributed attack)
    - Failures across many accounts (credential stuffing)

**Unusual access patterns**:

- Logins from unexpected countries
- Login from new IP immediately after password change
- Multiple concurrent sessions from different locations
- MFA bypass attempts

**Account compromise indicators**:

- Failed logins followed by successful login with MFA
- Login from unusual User Agent (different device)
- Sudden spike in access from single account
- Login to sensitive applications outside business hours

**Use case**: Investigate "Why can't this user log in?"

- Search by Email: user@example.com
- Set date range: Last 24 hours
- Review Event Type column for "failed" events
- Click Details to see full error message
- Check Provider Name (correct IdP configured?)
- Review IP Address and User Agent (correct user device?)

#### Exporting Event Logs

**Export options**:

- Click **Export Login Events** button
- Choose format: JSON or CSV
- Specify limit (max 1,000,000 events)
- File downloads automatically

**Use cases**:

- Security incident investigation
- Compliance reporting (access logs)
- Trend analysis (login patterns)
- User access reports

---

### Job Logs

**Location**: Monitoring > Job Logs

Job logs show scheduled tasks, data processing, and cleanup operations.

#### What is Logged

**Job types**:

- **Trim jobs**: Automatic log cleanup based on retention policies
- **Email jobs**: Batch email sending operations
- **Import jobs**: User/group bulk imports
- **Export jobs**: Data export operations
- **Archive jobs**: Audit log archiving
- **Sync jobs**: External system integrations
- **Cleanup jobs**: Expired token/session removal

**Job details**:

- **Job Id**: Unique identifier
- **Job Name**: Human-readable name
- **Job Type**: Category of job
- **Message**: Job status or error message
- **Latest Status**: success, failed, running, pending
- **Created At**: When job started
- **Updated At**: Last status change
- **Stacktrace**: Error details (for failed jobs)

#### Viewing Job Logs

**Table columns**:

- **Job Id**: Unique identifier
- **Job Name**: Descriptive name
- **Job Type**: Job category
- **Message**: Status or error message (click to copy full message)
- **Latest Status**: Current state
- **Created At**: Start time
- **Updated At**: Last update
- **Details**: Full job information
- **Stacktrace**: Error details (for failures)

**Default view**: Last 7 days of job executions.

#### Searching and Filtering Job Logs

**Search options**:

- **Job Name**: Find specific job by name
- **Job Type**: Filter by job category
- **Message**: Search error messages or status
- **Latest Status**: Filter by success, failed, running, pending

**Natural language queries**:

- 'the job name is "audit log trim"'
- "job type contains 'email' and status is 'failed'"
- "the job was created in the last 24 hours"

#### Monitoring Job Health

**Failed jobs**:

- Filter by Latest Status: "failed"
- Review Message column for error details
- Click Stacktrace to view full error
- Determine if issue is:
    - Configuration error (needs admin fix)
    - Temporary failure (will retry)
    - Data validation error (requires data fix)

**Long-running jobs**:

- Filter by Latest Status: "running"
- Check Created At vs. current time
- If running > expected duration, may indicate:
    - Large dataset processing
    - Performance issue
    - Hung job (may need restart)

**Recurring failures**:

- Same job failing repeatedly suggests systematic issue
- Review Message for pattern
- Check job configuration
- May require super admin assistance

**Use case**: "Why didn't the audit log trim job run last night?"

- Search Job Name: "audit log trim"
- Set date range: Last 24 hours
- Check Latest Status
- If failed, review Message and Stacktrace
- If not present, job may be disabled or misconfigured

#### Exporting Job Logs

**Export options**:

- Click **Export Job Logs** button
- Choose format: JSON or CSV
- Specify limit (max 1,000,000 logs)
- File downloads automatically

---

### Notification Logs

**Location**: Monitoring > Notification Logs

Notification logs track system email, including invitations, password resets, and alerts.

#### What is Logged

**Email types**:

- **Invitation emails**: Group invitations, access request invitations
- **Password reset emails**: Password reset links
- **MFA enrollment**: TOTP setup instructions
- **Access request notifications**: Approval requests to managers
- **Alert emails**: User alert settings (login notifications)
- **System notifications**: Admin announcements
- **Welcome emails**: New user onboarding

**Email details**:

- **Subject**: Email subject line
- **Recipient**: To address
- **CC**: Carbon copy recipients
- **BCC**: Blind carbon copy recipients
- **Timestamp**: When email was sent
- **Status**: sent, failed, pending, bounced
- **Category**: Type of email
- **Body**: Full email content (click to copy)

#### Viewing Notification Logs

**Table columns**:

- **Subject**: Email subject
- **Recipient**: Primary recipient
- **Timestamp**: Date and time sent
- **Status**: Delivery status
- **Category**: Email type/purpose
- **Details**: Full email body (click to copy)
- **CC**: Additional recipients (hidden by default)
- **BCC**: Hidden recipients (hidden by default)

**Default view**: Last 7 days of sent emails.

**Column customization**: Show/hide CC and BCC columns as needed.

#### Searching and Filtering Notification Logs

**Search options**:

- **Subject**: Find emails by subject line
- **Recipient**: Filter by email address
- **Status**: Filter by sent, failed, bounced
- **Category**: Filter by email type

**Natural language queries**:

- "the email subject contains 'totp reset' and the timestamp is from yesterday"
- "recipient is user@example.com and status is failed"
- "category is invitation and the log was created last week"

#### Monitoring Email Delivery

**Failed email delivery**:

- Filter by Status: "failed"
- Review Subject and Recipient
- Common failure reasons:
    - Invalid recipient address
    - SMTP configuration error
    - Recipient mail server rejected email
    - Email content triggered spam filter

**Bounced emails**:

- Filter by Status: "bounced"
- Indicates recipient mailbox doesn't exist or is full
- Update user's email address
- Verify email address spelling

**Email delivery delays**:

- Email shows Status: "pending" for extended period
- May indicate SMTP server issues
- Check tenant email settings
- Verify SMTP server connectivity

**Use case**: "Did the user receive the password reset email?"

- Search Recipient: user@example.com
- Search Subject: "password reset"
- Set date range: Last 24 hours
- Check Status column
- If "failed" or "bounced", review error details
- If "sent", email was successfully delivered to mail server

#### Exporting Notification Logs

**Export options**:

- Click **Export Notification Logs** button (if available)
- Choose format: JSON or CSV
- Specify limit (max 1,000,000 logs)
- File downloads automatically

**Note**: Email bodies can be large; JSON export may produce large files.

---

### Sessions

**Location**: Monitoring > Sessions

Sessions show and manage active user authentication sessions.

#### What is Tracked

**Session information**:

- **Session ID**: Unique session identifier
- **Protocol**: OIDC or SAML
- **Client ID**: Application session is for
- **User ID**: User who owns this session
- **Created At**: When session was established
- **Status**: active, expired, revoked

**Session lifecycle**:

- **Created**: User logs in successfully
- **Active**: User has valid session
- **Expired**: Session lifetime exceeded
- **Revoked**: Admin manually revoked session

#### Viewing Sessions

**Table columns**:

- **Session ID**: Unique identifier
- **Protocol**: OIDC or SAML
- **Client ID**: Application (clickable to view client details)
- **User ID**: User (clickable to view user details)
- **Created At**: Session start time
- **Status**: Current state (active, expired, revoked)
- **Details**: Full session information
- **Remove**: Revoke session (delete icon)

**Default search**: Search by User ID (numeric).

#### Searching Sessions

**Search options**:

- **Session ID**: Find specific session
- **Client ID**: View sessions for specific application
- **User ID**: Find all sessions for a user
- **Status**: Filter by active, expired, revoked

#### Managing Sessions

**Viewing session details**:

- Click **Details** icon
- View full session information:
    - Token claims
    - Scopes granted
    - Session parameters
    - Grant information

**Revoking a session**:

- Click **Remove** (delete) icon
- Confirm revocation
- Session is immediately invalidated
- User must re-authenticate to continue

**Use cases for session revocation**:

- **Security incident**: Compromised account, force re-authentication
- **Account lockout**: User violated policy, remove access
- **Role change**: User's permissions changed, force fresh token
- **User request**: User wants to log out all devices
- **Device lost**: User's device was stolen or lost

**Mass session revocation**:

- Filter sessions by User ID
- Revoke all sessions for that user
- User must re-authenticate everywhere
- **Use case**: Account takeover response

**Use case**: "How many active sessions does this user have?"

- Search by User ID: 123
- Filter by Status: "active"
- Review Client ID column to see which applications
- Check Created At to see session ages
- Revoke old sessions if needed

---

### Grants

**Location**: Monitoring > Grants

Grants show OAuth 2.0 and OIDC authorization records.

#### What is Tracked

**Grant information**:

- **Grant ID**: Unique identifier
- **Email**: User who granted consent
- **Provider**: Identity provider used
- **Client**: Application granted access to
- **Session ID**: Associated session
- **Created At**: When consent was given
- **Status**: active, expired, revoked

**Grant lifecycle**:

- **Created**: User consents to application access
- **Active**: Grant is valid, app can request tokens
- **Expired**: Grant lifetime exceeded
- **Revoked**: User or admin revoked consent

#### Viewing Grants

**Table columns**:

- **Grant ID**: Unique identifier
- **Email**: User (clickable to view user details)
- **Provider**: Identity provider
- **Client**: Application name (clickable to view client details)
- **Session ID**: Associated session
- **Created At**: Consent timestamp
- **Status**: Current state
- **Details**: Full grant information

**Default search**: Search by Email.

#### Searching Grants

**Search options**:

- **Email**: Find grants for specific user
- **Provider**: Filter by identity provider
- **Client**: View grants for specific application
- **Status**: Filter by active, expired, revoked

#### Understanding Grants

**What is a grant?**

- User consents to application accessing their data
- Application can request access tokens using the grant
- Grants have limited lifetime (can expire)
- Users can revoke grants at any time

**Grant vs. Session**:

- **Session**: Authenticated user context
- **Grant**: Authorization to access resources
- One session can have multiple grants
- Grants can outlive individual sessions

**Refresh token grants**:

- Long-lived grants enable refresh tokens
- Application can get new access tokens without re-authentication
- Grants persist even after user logs out
- Revoking grant invalidates all refresh tokens

**Use case**: "Which applications does this user have active grants for?"

- Search by Email: user@example.com
- Filter by Status: "active"
- Review Client column
- Applications listed can access user's resources
- Revoke grants for unused applications

---

## Natural Language Queries

**All log dashboards** support natural language queries (NLQ) for filtering in plain English.

### How to Use

- Look for **"Ask a question"** or NLQ search bar
- Type your question in plain English
- System converts to database query
- Results are filtered automatically

### Query Examples

**Audit Logs**:

- "action is 'update IdentityProvider' and the timestamp is from last month"
- "the actor is like example.com"
- "the target is 'test role'"
- "show me all delete actions from yesterday"
- "who modified the google provider last week"

**Event Logs**:

- "the event type is login and the log was created in the last 7 days"
- "client is reportmgr and the log was created one week ago"
- "show failed logins from IP xxx.xxx.xxx.xxx"
- "mfa events for user@example.com yesterday"
- "login events where provider is google"

**Job Logs**:

- 'the job name is "audit log trim"'
- "job type contains 'email' and status is 'failed'"
- "the job was created in the last 24 hours"
- "show running jobs older than 1 hour"

**Notification Logs**:

- "the email subject contains 'totp reset' and the timestamp is from yesterday"
- "recipient is user@example.com and status is failed"
- "category is invitation and the log was created last week"

### Query Syntax Tips

**Time expressions**:

- "from yesterday", "last week", "last month"
- "in the last 7 days", "in the last 24 hours"
- "one week ago", "two days ago"

**Comparison operators**:

- "is", "is not"
- "contains", "does not contain"
- "like", "not like" (partial matching)

**Combining conditions**:

- Use "and" to combine multiple conditions
- Example: "event type is login and created on is from last week and email is user@example.com"

**Field names**:

- Use column names from the table
- Case insensitive
- Example: "Event Type", "event type", "eventType" all work

---

## Exporting Logs

**Export formats**: JSON (complete data) or CSV (spreadsheet-compatible).

### JSON Export

**Advantages**:

- Complete data preservation (all fields)
- Machine-readable for automated processing
- Nested objects preserved
- Suitable for archiving and compliance

**Use cases**:

- Long-term audit log retention
- Compliance archiving (SOC 2, HIPAA)
- Data migration to SIEM systems
- Forensic investigation

### CSV Export

**Advantages**:

- Spreadsheet-compatible (Excel, Google Sheets)
- Human-readable format
- Easy sorting and filtering in spreadsheet
- Suitable for reporting and analysis

**Limitations**:

- Nested objects flattened or truncated
- Some field data may be simplified

**Use cases**:

- Management reporting
- Trend analysis in Excel
- Quick review and sharing
- Non-technical stakeholder reports

### Export Best Practices

- **Regular exports**:
    - Export audit logs weekly or monthly
    - Schedule exports before data retention cleanup
    - Store exports in secure, backed-up location

- **Retention compliance**:
    - Check regulatory requirements (7 years for SOC 2, etc.)
    - Export before automatic log trimming
    - Document export schedule and storage location

- **Security**:
    - Exports contain sensitive data (emails, IPs, changes)
    - Encrypt export files at rest
    - Limit access to export archives
    - Securely delete old exports per policy

- **Export size management**:
    - Limit exports to reasonable size (< 1M records)
    - Use date range filters to reduce size
    - Consider incremental exports (daily/weekly) vs. full exports

- **Verification**:
    - Test restore process for audit log archives
    - Verify export completeness (record counts)
    - Document export and restore procedures

---

## Security and Compliance

### Security Monitoring

**Daily tasks**:

- Review Event Logs for failed login attempts
- Monitor for unusual IP addresses or geolocations
- Check for MFA bypass attempts
- Review active Sessions for suspicious activity

**Weekly tasks**:

- Audit Logs: Review high-privilege actions (IdP changes, role grants)
- Job Logs: Verify trim jobs completed successfully
- Notification Logs: Check for failed password reset emails (social engineering indicator)
- Export logs for weekly archive

**Monthly tasks**:

- Review Audit Logs
- Review all Grants for unused applications
- Revoke old Sessions (> 30 days)
- Verify email delivery health (Notification Logs)

### Incident Response

**Suspected account compromise**:

- **Event Logs**: Filter by user email
- Review login locations, times, devices
- Check for unusual application access
- **Sessions**: Revoke all user sessions
- **Grants**: Revoke all user grants
- **Audit Logs**: Review any changes user made
- Reset user password and require MFA

**Configuration tampering**:

- **Audit Logs**: Filter by Target (affected resource)
- Review Old Entity vs. New Entity
- Identify Actor who made change
- Verify change was authorized
- Revert unauthorized changes
- If unauthorized, investigate Actor account compromise

**Bulk operation failure**:

- **Job Logs**: Find failed job
- Review Message and Stacktrace
- Determine if data error or config error
- Fix underlying issue
- Manually retry job or wait for next scheduled run

### Compliance Requirements

**SOC 2 Type II**:

- Continuous monitoring of administrative changes
- Audit log retention (minimum 1 year, recommend 7 years)
- Regular review of access grants and sessions
- Evidence of security monitoring (incident reports)

**HIPAA**:

- Audit trail of all PHI access (Event Logs)
- Administrative action audit (Audit Logs)
- Session tracking for accountability
- Log retention (6 years)

**GDPR**:

- Audit trail of personal data access
- Evidence of user consent (Grants)
- Data processing records (Job Logs)
- Right to be forgotten audit (Audit Logs)

### Audit Log Best Practices

- **Review regularly**:
    - Don't wait for incidents to review logs
    - Establish routine review schedule
    - Assign responsibility for log review

- **Alert on anomalies**:
    - Set up monitoring for unusual patterns
    - Alert on bulk deletions
    - Monitor failed login spikes
    - Watch for after-hours admin activity

- **Protect audit logs**:
    - Audit logs themselves are sensitive
    - Limit who can view audit logs
    - Prevent audit log deletion
    - Export regularly for protection against tampering

- **Document procedures**:
    - Who reviews logs and how often
    - What constitutes an incident
    - Escalation procedures
    - Export and retention procedures

- **Maintain integrity**:
    - Audit logs should be immutable
    - Export to write-once storage for compliance
    - Hash exports to prove integrity
    - Maintain chain of custody for exports

---

## Additional Resources

- [Users and Groups Management Guide](users-groups-admin-guide.md)
- [Access Requests Guide](access-requests-guide.md)
- [SSO Integration Guide](sso-integration-guide.md)
- [Tenant Administrator Guide](tenant-admin-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)

---

## Getting Help

For assistance with monitoring and logging:

- **Check Job Logs** for automated task failures
- **Review Event Logs** for authentication issues
- **Export logs** for detailed analysis offline
- **Use natural language queries** for complex filtering
- **Contact your admin team** for help interpreting logs or investigating incidents

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators, Security Operations  
**Scope**: Monitoring, logging, audit trails, and compliance

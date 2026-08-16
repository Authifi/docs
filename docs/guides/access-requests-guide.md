# Access Requests and Delegated Administration Guide

Tenant administrators manage access requests, self-service workflows, and delegated administration in the Authifi UI.

## Table of Contents

- [Overview](#overview)
- [Access Requests Menu Options](#access-requests-menu-options)
  - [View Access Requests](#view-access-requests)
  - [Request Templates](#request-templates)
  - [User-Managed Roles (UMRS)](#user-managed-roles-umrs)
  - [Access Grants](#access-grants)
  - [Group Invitations](#group-invitations)
  - [SSH Secrets](#ssh-secrets)
  - [Batch Invite](#batch-invite)
- [Request Template Configuration](#request-template-configuration)
  - [RBAC Group Access Template](#rbac-group-access-template)
  - [Resource Access Template (UMRS)](#resource-access-template-umrs)
  - [Extension Request Templates](#extension-request-templates)
- [UMRS Role Configuration](#umrs-role-configuration)
- [Security Best Practices](#security-best-practices)

---

## Overview

**Access Requests** supports:

- **Self-service access**: Users request access; approvers grant or deny
- **Delegated administration**: Let non-admins manage specific resources
- **Time-limited access**: Automatic expiration for temporary permissions
- **Audit trail**: Record of access grants and approvals
- **Bulk operations**: Onboard groups of users

It applies least-standing-privilege controls through:

- Just-in-time access (request when needed, not granted permanently)
- Time-limited grants (automatic expiration)
- Peer approval (non-admin managers can approve)
- Self-service (reduces admin bottlenecks)

---

## Access Requests Menu Options

### View Access Requests

**Location**: Access Requests > View Access Requests

Use this dashboard to view, create, approve, and reject access requests.

#### What you can do

- **View all access requests**: See pending, approved, and rejected requests
- **Create access requests**: Submit new requests on behalf of users
- **Review requests**: Examine request details before approval
- **Approve requests**: Grant access to approved requests
- **Reject requests**: Deny access with reason
- **Filter and search**: Find specific requests by user, status, date range, or resource

#### Request Table Columns

- **Access Request Name**: The template name for this request
- **User**: Email of user requesting access
- **Resource ID**: Identifier of the requested resource
- **Request Date**: When request was submitted
- **Expired**: Whether the request has expired
- **Request Expiration Date**: When request expires if not approved
- **Status**: `pending`, `approved`, or `rejected`
- **Details**: Click to view full request details and take action

#### Filtering Access Requests

**Status filter**:

- **Pending**: Awaiting approval
- **Approved**: Access has been granted
- **Rejected**: Access was denied
- **Any**: Show all requests

**Expiration filter**:

- **Expired**: Requests past their approval deadline
- **Not Expired**: Active requests still awaitable approval
- **Any**: Show all

**Date range filter**:

- Filter by request creation date
- Presets: Today, Yesterday, Last 7/30 days, This/Last Month
- Custom range: Select start and end dates

**Search**: Free-text search across request name, user, and access type

#### Request Lifecycle

- **Submitted**: User or admin creates an access request
- **Pending**: Request awaits approval from authorized approver
- **Approved**: Approver grants access
    - User/group is added to target group or granted UMRS role
    - Access becomes active immediately
- **Rejected**: Approver denies access
    - No permissions granted
    - User notified of rejection
- **Expired**: Request deadline passed without approval
    - Request cannot be approved after expiration
    - User must submit a new request

#### Creating an Access Request

- Click **+ ADD NEW** button
- Select user who needs access
- Choose access template (pre-configured by admin)
- Provide justification (optional)
- Submit request
- Request appears in pending state for approvers

Use case: An admin creates a request for a user without self-service access.

#### Approving/Rejecting Requests

- Click **Details** icon on a pending request
- Review request details:
    - Who is requesting
    - What access they need
    - Why (justification)
    - When requested
- Click **Approve** or **Reject**
- Optionally provide comment
- Access is granted/denied immediately

**Security recommendations**:

- Review justification before approving
- Verify requester's identity through separate channel for sensitive access
- Document approval decision in comment field
- Set up approval workflows requiring multiple approvers for high-privilege access
- Monitor for unusual request patterns (bulk requests, repeated rejections)

---

### Request Templates

**Location**: Access Requests > Request Templates

Use request templates to define access patterns that users can request.

#### What you can do

- **View templates**: See all configured access request templates
- **Create templates**: Define new requestable access patterns
- **Edit templates**: Update template configuration
- **Delete templates**: Remove unused templates

#### Template Types

**RBAC Group Access**:

- User requests membership in a specific group
- Upon approval, user is added to the group
- User inherits all group roles and permissions
- **Use case**: "Request Developer Access" → adds user to "developers" group

**Resource Access (UMRS)**:

- User requests a specific UMRS role on a resource
- Upon approval, user gets role grant on that resource
- Supports resource-level access control
- **Use case**: "Request Project Alpha Access" → grants "viewer" role on "project-alpha" resource

**RBAC Group Extension Request**:

- User requests to extend their membership in a group they're already in
- Prevents automatic removal upon expiration
- **Use case**: Contractor extends "external-developers" membership for another quarter

**Resource Extension Request (UMRS)**:

- User requests to extend their UMRS role grant
- Extends expiration date on existing grant
- **Use case**: Project team member extends access beyond initial timeframe

#### Template Table Columns

- **Access Request Name**: Template display name
- **Application**: Associated client application (optional)
- **Target Resource**: What access this grants (group name or UMRS role)
- **Settings**: Edit template configuration
- **Delete**: Remove template

#### Creating a Template

- Click **+ ADD NEW** dropdown
- Select template type:
    - **RBAC Group Access**: Group membership request
    - **Resource Access**: UMRS role grant request
    - **RBAC Group Extension Request**: Extend group membership
    - **Resource Extension Request**: Extend UMRS grant
- Configure template (see [Request Template Configuration](#request-template-configuration))
- Click **Save**

**Security recommendations**:

- Create templates only for access that should be self-serviceable
- Require approval for all sensitive access (don't use auto-approval)
- Set appropriate expiration times for approval windows
- Use clear, descriptive template names
- Document approval workflow in template description

---

### User-Managed Roles (UMRS)

**Location**: Access Requests > User-Managed Roles

Define roles that designated managers can grant without full admin access.

**What is UMRS?**

User-Managed Role System (UMRS) supports **delegated administration**:

- Project leads can grant project-specific access
- Team managers can control team resource access
- Non-admins can manage access to resources they own
- Reduces admin bottleneck for routine access grants

#### What you can do

- **View UMRS roles**: See all user-managed roles
- **Create UMRS roles**: Define new delegated roles
- **Edit UMRS roles**: Update role configuration
- **Delete UMRS roles**: Remove unused roles
- **Configure managers**: Designate who can grant this role

#### UMRS Role Table Columns

- **Name**: Role identifier
- **Description**: Role purpose
- **Resource Server**: API/resource this role applies to
- **Settings**: Edit role configuration
- **Delete**: Remove role

#### Creating a UMRS Role

- Click **+ ADD User Role**
- Configure role:
    - **Name\***: Unique role identifier
    - **Description\***: What this role grants access to
    - **Managers Group\***: Group whose members can grant this role
    - **Resource Server\***: API or resource this role controls
    - **Allow Grant Extension Requests**: Users can request to extend their grants
- Click **Save**

**Example configuration**:

```
Name: project-alpha-viewer
Description: View-only access to Project Alpha resources
Managers Group: project-alpha-leads
Resource Server: Project Management API
Allow Grant Extension Requests: Yes
```

Result: Members of the "project-alpha-leads" group can grant the "project-alpha-viewer" role to users without full admin privileges.

#### UMRS vs. Regular Roles

| Feature           | Regular Roles | UMRS Roles          |
| ----------------- | ------------- | ------------------- |
| **Who can grant** | Admins only   | Designated managers |
| **Scope**         | Tenant-wide   | Resource-specific   |
| **Expiration**    | Permanent     | Can be time-limited |
| **Extension**     | N/A           | Can allow requests  |
| **Delegation**    | No            | Yes                 |

**Use cases**:

- **Project-based access**: Project managers control project resource access
- **Team resources**: Team leads manage team-specific tools
- **Departmental apps**: Department heads grant access to department resources
- **Customer resources**: Account managers control customer data access

UMRS provides:

- Reduces admin workload for routine access grants
- Lets domain experts manage their resources
- Maintains audit trail of who granted what
- Enables fine-grained, resource-level access control

---

### Access Grants

**Location**: Access Requests > Access Grants

View and manage UMRS role grants for users and groups.

The dashboard has two tabs:

- **User Grants**: Individual user assignments to UMRS roles
- **Group Grants**: Group assignments to UMRS roles

#### User Grants Tab

**What you can see**:

- **Resource Server**: API or resource
- **Resource**: Specific resource instance (e.g., "project-123")
- **Role**: UMRS role granted
- **User**: User who has this grant
- **Expires**: Expiration date (or "never")
- **Settings**: Edit expiration
- **Delete**: Revoke grant

**What you can do**:

- **Add user to role**: Click **+ ADD User to Role**
  - Select UMRS role
  - Select user
  - Set resource ID
  - Set expiration date (optional)
  - User immediately gets access
- **Edit expiration**: Click **Settings** icon
  - Update expiration date
  - Extend or shorten access duration
- **Revoke grant**: Click **Delete** icon
  - Remove user's access immediately
  - User loses role-based permissions

#### Group Grants Tab

**What you can see**:

- **Resource Server**: API or resource
- **Resource**: Specific resource instance
- **Role**: UMRS role granted to group
- **Group**: Group that has this grant
- **Expires**: Expiration date (or "never")
- **Settings**: Edit expiration
- **Delete**: Revoke grant

**What you can do**:

- **Add group to role**: Click **+ ADD Group to Role**
  - Select UMRS role
  - Select group
  - Set resource ID
  - Set expiration date (optional)
  - All group members immediately get access
- **Edit expiration**: Update group grant expiration
- **Revoke grant**: Remove group's access

#### User Grants vs. Group Grants

**Use user grants when**:

- Individual user needs exceptional access
- One-off access requirement
- Temporary access for specific person

**Use group grants when**:

- Multiple users need same access
- Access aligns with organizational structure (team, project)
- Recurring pattern (many people will need this over time)

**Best practice**: Prefer group grants over individual user grants for maintainability and scalability.

**Security recommendations**:

- Set expiration dates for all grants (avoid permanent grants)
- Review grants quarterly and remove unnecessary access
- Monitor for grants that never expire
- Use shortest reasonable expiration period
- Require re-approval for extensions

---

### Group Invitations

**Location**: Access Requests > Group Invitations

Send email invitations for group membership, with optional confirmation.

#### What you can do

- **View invitations**: See all group invitations (pending, accepted, expired)
- **Create invitations**: Invite users to join groups via email
- **Monitor status**: Track which invitations are pending, accepted, or expired
- **Delete invitations**: Cancel pending invitations

#### Invitation Table Columns

- **Status**: `pending`, `accepted`, `expired`
- **Group**: Target group user is invited to join
- **Email**: Invitee's email address
- **Created By**: Who sent the invitation
- **Created At**: When invitation was sent
- **Updated At**: Last status change
- **Expires (minutes)**: Time until invitation expires
- **Group Access Expiration Date**: When group membership will expire (if set)
- **Delete**: Cancel invitation

#### Creating a Group Invitation

- Click **+ ADD INVITATION**
- Fill in invitation details:
    - **Group\***: Select group to invite user to
    - **User Email\***: Email address of invitee (can autocomplete existing users or enter new email)
    - **Identity Issuer\***: Which IdP the user will authenticate with
    - **Application URL\***: Redirect URL after accepting invitation
    - **Expiration (minutes)**: How long invitation remains valid
    - **Require Invitation Confirmation**: Whether user must click link to accept
    - **Invitation Template\***: Email template for invitation message
    - **Confirmation Template**: HTML page shown after accepting (if confirmation required)
    - **Set membership expiration date**: Optional expiration for group membership
- Click **Save**
- Invitation email is sent immediately

#### Invitation Workflow

**Without confirmation**:

- Invitation is created
- Email is sent to user
- User is automatically added to group
- User can access group resources immediately

**With confirmation required**:

- Invitation is created
- Email is sent with confirmation link
- User clicks link and sees confirmation page
- User is added to group after confirmation
- User can access group resources

**Expiration behavior**:

- Invitation expires after specified time
- Expired invitations cannot be accepted
- Must create new invitation if expired

**Membership expiration**:

- If set, user's group membership expires on specified date
- User is automatically removed from group at expiration
- User can request extension if group allows extension requests

**Use cases**:

- **Onboarding**: Invite new employees to "employees" group
- **Project teams**: Invite contractors to "project-x-contractors" group with expiration
- **Temporary access**: Invite auditors to "auditors" group for limited time
- **Self-service**: Allow users to accept invitations without admin intervention

**Security recommendations**:

- Set reasonable expiration times (1-7 days for invitations)
- Use confirmation for sensitive group invitations
- Set membership expiration for temporary access
- Monitor for expired invitations and clean up
- Require identity issuer for non-existing users to prevent wrong IdP selection

---

### SSH Secrets

**Location**: Access Requests > SSH Secrets

Generate SSH credentials for users who need access to remote servers or command-line interfaces.

**Availability**: Requires `auth.admin.usersshsecret` scope (elevated permission).

#### What you can do

- **Create SSH access**: Generate SSH key pair and configuration for a user
- **Download credentials**: Receive JSON file with SSH private key, public key, and connection details

#### Creating SSH Access

- Navigate to **SSH Secrets** dashboard
- Fill in the form:
    - **User Email\***: Search and select the user who needs SSH access
        - Autocomplete searches across all tenant users
        - Shows email, name, and identity issuer
    - **SSH Server\***: Hostname or IP of the SSH server
        - Example: `ssh.example.com`, `10.0.1.50`
    - **SSH Username\***: Username for SSH login
        - Example: `ubuntu`, `ec2-user`, user's system username
- Click **Create SSH Secret**
- JSON file downloads automatically with credentials

#### Downloaded Credentials Format

```json
{
  "userId": 123,
  "userEmail": "user@example.com",
  "sshServer": "ssh.example.com",
  "sshUsername": "ubuntu",
  "publicKey": "ssh-rsa AAAAB3NzaC1yc2EA...",
  "privateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
  "sshConfig": "Host ssh.example.com\n  User ubuntu\n  IdentityFile ~/.ssh/auth_rsa"
}
```

#### Installing SSH Credentials (User Instructions)

**For users receiving SSH access**:

- Save the `privateKey` to `~/.ssh/auth_rsa`
- Set permissions: `chmod 600 ~/.ssh/auth_rsa`
- Add public key to authorized_keys on server (admin task) or use provided config
- Connect: `ssh -i ~/.ssh/auth_rsa ubuntu@ssh.example.com`

**Server-side setup** (requires server admin):

```bash
# Add user's public key to authorized_keys
echo "ssh-rsa AAAAB3NzaC1yc2EA..." >> /home/ubuntu/.ssh/authorized_keys
chmod 600 /home/ubuntu/.ssh/authorized_keys
```

**Security warnings**:

**Critical**: The downloaded private key cannot be retrieved again. If it is lost, generate new credentials.

**Secret material**: Treat the downloaded JSON as highly sensitive because it contains a private key.

**One-time download**: Transmit the file securely, or have the user generate credentials in the self-service portal.

**Security recommendations**:

- **Rotate SSH keys regularly** (every 90-180 days)
- **Audit SSH key creation** (elevated privilege operation)
- **Secure private key storage**: Use password-protected key files or SSH agent
- **Revoke unused keys**: Delete from server's `authorized_keys` when no longer needed
- **Set key expiration**: Implement key expiration policies on SSH servers
- **Monitor SSH access logs**: Track who accesses what via SSH
- **Use separate keys per server**: Don't reuse keys across environments
- **Require strong passphrases**: If possible, generate password-protected keys

---

### Batch Invite

**Location**: Access Requests > Batch Invite

Invite multiple users to a group in one operation for team or class onboarding.

#### What you can do

- **View batch operations**: See all batch invite jobs with status tracking
- **Create batch invites**: Invite many users at once with a single email template
- **Monitor progress**: Track success/failure/pending counts
- **View details**: Examine individual invitation results
- **Copy batch jobs**: Reuse configuration for similar operations

#### Batch Operations Table Columns

- **Requested By User**: Who initiated the batch operation
- **Invitation Group**: Target group for invitations
- **Success**: Count of successful invitations (clickable for details)
- **Failure**: Count of failed invitations (clickable for details)
- **Pending**: Count of pending invitations (clickable for details)
- **Total Requests**: Total number of users in batch
- **Status**: Overall batch job status (`processing`, `completed`, `failed`)
- **Description**: Batch operation description
- **Created At**: When batch was initiated
- **Details**: View full batch details
- **Copy**: Clone configuration for similar batch operation

#### Creating a Batch Invite

- Click **+ BATCH INVITE**
- Fill in batch configuration:
    - **Group\***: Select target group (autocomplete search)
    - **Users\***: JSON array of email addresses
        - **Format**: `["user1@example.com", "user2@example.com", "user3@example.com"]`
        - Monaco code editor with syntax validation
    - **Redirect URL\***: Where users land after accepting invitation
    - **Identity Issuer\***: IdP users will authenticate with
    - **Description\***: Purpose of this batch operation
    - **Email Template\***: Template for invitation emails
    - **Require Confirmation**: Whether users must click link to accept
    - **Expiration (minutes)**: How long invitations remain valid (if confirmation required)
    - **Confirmation Template**: HTML page for invitation acceptance (if confirmation required)
- Click **Save**
- Batch processing begins asynchronously

#### Batch Processing

**Lifecycle**:

- **Submitted**: Batch job created and queued
- **Processing**: System sends invitations sequentially
- **Completed**: All invitations processed (may include some failures)
- **Failed**: Batch job encountered fatal error

**Monitoring progress**:

- Click **Success/Failure/Pending** counts to see specific invitations
- Click **Details** to view full batch configuration and all results
- **Copy** button allows repeating similar batch operations

**Handling failures**:

- Review failure details (invalid email, user already in group, quota exceeded)
- Fix issues and create new batch for failed entries
- Check logs for systematic issues

#### Batch Invite JSON Format

```json
[
  "alice@example.com",
  "bob@example.com",
  "charlie@example.com",
  "diana@example.com"
]
```

Validation: The JSON value must be an array of email-address strings.

**Use cases**:

- **Class enrollment**: Invite 50 students to "course-2025-spring" group
- **Department onboarding**: Add 20 new hires to "engineering" group
- **Event access**: Grant 100 attendees access to "conference-2025" group
- **Partner onboarding**: Invite partner organization users to collaboration group

**Best practices**:

- Test with small batch first (2-3 users) before large operations
- Use meaningful descriptions (e.g., "Q1 2025 Intern Onboarding")
- Monitor batch progress for failures
- Keep batch sizes reasonable (< 1000 users per batch)
- Have email addresses validated before batching
- Use confirmation for new users, skip for existing users

**Security recommendations**:

- Verify email list before submission (avoid typos)
- Ensure all recipients should actually get this access
- Use group expiration for temporary batch access
- Audit batch operations regularly (powerful capability)
- Limit who can create batch invites (consider requiring approval)

---

## Request Template Configuration

### RBAC Group Access Template

**Purpose**: Allow users to request membership in a specific group.

#### Configuration Fields

- **Requested Group\***
  - The group users will be added to upon approval
  - Autocomplete search with filtering
  - Shows group name and description
  - **Create Group button**: Quick-create group if it doesn't exist

- **Request Template Display Name\***
  - User-facing name for this access request option
  - Example: "Request Developer Access", "QA Environment Access"
  - Should clearly describe what access is granted

- **Inviter/Approver Group\***
  - Group whose members can approve requests
  - Members of this group receive approval notifications
  - Can invite others directly (bypass request workflow)
  - **Create Group button**: Quick-create approver group if needed

- **Accept/Approve Within\***
  - Deadline for approval
  - After this time, request expires and cannot be approved
  - Units: days or minutes
  - Example: 7 days, 1440 minutes (1 day)
  - **Security note**: Shorter windows reduce exposure of pending requests

- **Invitation Email Template\***
  - Email template used when sending invitation/approval emails
  - Select from configured email templates
  - Should include:
    - What access is being granted
    - How to accept or request
    - Expiration information

- **Client Application**
  - Associate this template with a specific application (optional)
  - Helps users understand what app they're getting access to
  - Used for audit and reporting

- **Description**
  - Internal description of template purpose
  - Not shown to requesters
  - **Use case**: Document approval requirements, escalation paths

**Example template**:

```
Template Display Name: Request Production Database Access
Requested Group: production-db-users
Approver Group: database-administrators
Accept/Approve Within: 3 days
Email Template: Standard Access Request
Client Application: Production DB Management Portal
Description: Grants read-only access to production databases. Requires DBA approval. Must have completed security training.
```

---

### Resource Access Template (UMRS)

**Purpose**: Allow users to request UMRS role grants on specific resources.

#### Configuration Fields

Similar to RBAC template, plus:

- **UMRS Role\***
  - Select from configured UMRS roles
  - Determines what permissions are granted
- **Resource Scope**
  - Whether user can specify resource ID
  - Or template is for a specific pre-defined resource

**Use case**: "Request Project Access" where users specify which project (resource ID) they need access to.

---

### Extension Request Templates

**Purpose**: Allow users to request extension of existing access beyond original expiration date.

**RBAC Group Extension**:

- User is already in group with expiration
- Requests to extend membership
- Approver can grant extension

**Resource Extension (UMRS)**:

- User has UMRS role grant with expiration
- Requests to extend grant
- Manager can approve extension

**Configuration**: Similar to access templates, but applied to existing grants rather than creating new ones.

**Security benefit**: Forces periodic re-approval of access, ensuring access is still appropriate.

---

## UMRS Role Configuration

### Creating a UMRS Role

The UMRS role creation dialog contains these fields:

#### Name\*

- Unique identifier for the role
- **Format**: Use descriptive, resource-scoped names
- **Examples**:
  - `project-alpha-admin`
  - `api-orders-viewer`
  - `resource-editor`
- **Naming convention**: `<resource>-<privilege-level>`

#### Description\*

- Human-readable explanation of what this role grants
- Shown to users when requesting access
- **Examples**:
  - "Full administrative access to Project Alpha resources"
  - "View-only access to Order Management API"
  - "Edit permissions for shared resources"

#### Managers Group\*

- Group whose members can grant this role
- This delegates admin power to non-admins.
- Autocomplete search with filtering
- **Security note**: Only assign manager rights to trusted groups

**Example scenario**:

```
UMRS Role: project-beta-contributor
Managers Group: project-beta-leads
Result: Members of "project-beta-leads" can grant "project-beta-contributor" role
```

#### Resource Server\*

- The API or resource system this role controls access to
- Select from configured resource servers (APIs)
- Determines what permissions this role can grant
- **Example**: "Project Management API", "Document Storage API"

#### Allow Grant Extension Requests

- Toggle to enable users to request access extensions
- When enabled, users with this role can request to extend their grant before expiration
- Managers receive extension requests for approval
- **Use case**: Project-based access where timelines may extend

Extensions should require approval; do not allow automatic extensions.

---

## Security Best Practices

### Access Request Security

- **Require approval for sensitive access**
    - Don't use auto-approval for privileged groups
    - Require justification in requests
    - Implement multiple-approver workflows for critical access
    - Monitor approval patterns (who approves what)

- **Set appropriate expiration windows**
    - Short approval windows (3-7 days) for routine access
    - Longer windows (30 days) for complex approvals requiring research
    - Expire requests that aren't approved promptly
    - Clean up expired requests regularly

- **Audit request activity**
    - Review approval patterns for abuse
    - Monitor for unusual request volumes
    - Track approver actions
    - Alert on rejected requests (may indicate attack or confusion)

- **Validate requesters**
    - Ensure requester identity is verified
    - Check for legitimate business need
    - Verify requester is using correct IdP
    - Watch for social engineering attempts

### UMRS Security

- **Carefully choose managers**
    - Manager groups wield delegated admin power
    - Only grant to trusted, trained individuals
    - Review manager group membership quarterly
    - Require security awareness training for managers

- **Scope UMRS roles narrowly**
    - Create resource-specific roles, not tenant-wide
    - Limit permissions in UMRS roles to minimum required
    - Don't create overly broad UMRS roles
    - Regular roles for broad access; UMRS for specific resources

- **Enforce time limits**
    - Always set expiration dates on UMRS grants
    - Require re-approval for extensions
    - Shorter expirations for higher-risk resources (30-90 days)
    - Longer expirations for stable team access (180-365 days)

- **Monitor delegation**
    - Audit who is granting what access
    - Watch for managers granting to themselves
    - Alert on unusual grant patterns
    - Review grant extensions for necessity

- **Separate privileges**
    - Don't make the same group both target and manager
    - Separate "can grant" from "has access"
    - Prevent circular delegation patterns

### Group Invitation Security

- **Verify email addresses**
    - Double-check email lists before batch operations
    - Prevent typos that send invitations to wrong people
    - Use domain validation (e.g., only @company.com)
    - Monitor for suspicious external domains

- **Use confirmation for new users**
    - Require invitation acceptance click
    - Prevents accidental group additions
    - Gives users visibility into what access they're getting
    - Creates audit trail of user consent

- **Set membership expiration**
    - Use for contractors, consultants, temporary staff
    - Review and re-approve before extension
    - Automatic cleanup when period ends
    - Reduces orphaned access

- **Monitor invitation abuse**
    - Track who is sending invitations
    - Alert on large batch operations
    - Review expired invitations (may indicate incorrect emails)
    - Watch for invitation spam patterns

### SSH Access Security

- **Treat as highly privileged**
    - SSH access is powerful (terminal access)
    - Require elevated admin scope for creation
    - Limit who can generate SSH credentials
    - Audit all SSH key generations

- **Secure private keys**
    - Transmitted once (in download)
    - User must protect private key
    - Consider password-protecting keys
    - Use SSH agent for key management

- **Rotate keys**
    - Regular rotation schedule (90-180 days)
    - Immediate rotation on compromise
    - Remove old keys from servers
    - Maintain key inventory

- **Monitor SSH usage**
    - Log SSH connections on servers
    - Alert on unusual access patterns
    - Correlate with user access grants
    - Investigate unauthorized access attempts

- **Server-side controls**
    - Implement server-side key expiration
    - Use SSH certificate authorities for short-lived certs
    - Restrict SSH access by IP/network
    - Enable 2FA for SSH (if supported)

### Operational Security

- **Document workflows**
    - Clear procedures for access requests
    - Approval SLAs (how quickly to respond)
    - Escalation paths for urgent requests
    - Training materials for approvers

- **Regular reviews**
    - Weekly: Review pending requests (avoid backlog)
    - Monthly: Audit approved access grants
    - Quarterly: Review template configurations
    - Annually: Audit access requests and grants

- **Automate where safe**
    - Low-risk access can be auto-approved
    - High-risk access requires human approval
    - Use approval rules based on group/resource sensitivity
    - Monitor automated approvals for abuse

- **Maintain audit trail**
    - All requests logged with timestamps
    - Approver identity recorded
    - Rejection reasons documented
    - Support forensic investigation

---

## Additional Resources

- [Users and Groups Management Guide](users-groups-admin-guide.md)
- [SSO Integration Guide](sso-integration-guide.md)
- [Tenant Administrator Guide](tenant-admin-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)

---

## Getting Help

For assistance with access requests and delegated administration:

- **Check audit logs** for request and approval history
- **Review request templates** to ensure they're configured correctly
- **Test workflows** in non-production before deploying
- **Monitor batch operations** for failures and investigate
- **Contact your admin team** for policy questions or approval escalations

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators, UMRS Managers  
**Scope**: Self-service access management and delegated administration

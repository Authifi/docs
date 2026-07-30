# Users and Groups Management Guide

This guide provides comprehensive instructions for tenant administrators on managing users and groups through the Authifi UI.

## Table of Contents

- [Overview](#overview)
- [Users and Groups Menu](#users-and-groups-menu)
  - [Users](#users)
  - [User Groups](#user-groups)
  - [Assigned Roles](#assigned-roles)
  - [Assigned Permissions](#assigned-permissions)
- [User Management Reference](#user-management-reference)
  - [Creating a User](#creating-a-user)
  - [User Settings Tab](#user-settings-tab)
  - [Groups Tab](#groups-tab)
  - [Roles Tab](#roles-tab)
  - [Permissions Tab](#permissions-tab)
  - [Profile Data Tab](#profile-data-tab)
  - [TOTP Tab](#totp-tab)
  - [Passkeys Tab](#passkeys-tab)
  - [SSH Keys Tab](#ssh-keys-tab)
- [User Group Management Reference](#user-group-management-reference)
  - [Creating a User Group](#creating-a-user-group)
  - [Group Settings Tab](#group-settings-tab)
  - [Members Tab](#members-tab)
  - [Access Roles Tab](#access-roles-tab)
  - [UMRS Grants Tab](#umrs-grants-tab)
- [Security Best Practices](#security-best-practices)

---

## Overview

The **Users and Groups** section enables you to:

- Create and manage user accounts
- Organize users into groups for access control
- Assign roles and permissions to users and groups
- Monitor and audit user access

**Security context**: User and group management is critical for maintaining least privilege access. Proper configuration ensures users have only the permissions they need while simplifying administration through group-based access control.

**Note**: For platform license management (super admins only), see the **[License Management Guide](license-management-guide.md)**.

---

## Users and Groups Menu

The **Users and Groups** menu provides comprehensive user and group management capabilities.

### Users

**Location**: Users and Groups > Users

**Purpose**: Manage individual user accounts within the tenant.

#### What you can do

- **View all users**: Table displays username, name, email, issuer, provider, creation date, user type
- **Create users**: Add new user accounts (manual creation or import)
- **Edit users**: Update user properties, assign groups/roles/permissions
- **Delete users**: Remove user accounts
- **Import users**: Bulk import from another tenant or JSON file
- **Export users**: Download user list for backup or reporting
- **View event logs**: See authentication and authorization events for a user

#### User Types

Users are categorized based on their privileges:

- **Super Admin**: Platform-wide administrator
- **Admin**: Tenant administrator
- **Privileged**: Member of privileged groups (elevated permissions)
- **User**: Standard user (no special privileges)

#### Creating a User

- Click **+ ADD USER**
- Complete the user creation dialog (see [User Management Reference](#user-management-reference))
- Click **Save**

#### User Operations

- **Settings**: Click settings icon to edit user properties
- **Delete**: Click delete icon to remove user
- **Event Logs**: Click binoculars icon to view user's authentication events
- **Export**: Download user list with filters

#### Importing Users

**From another tenant**:

- Click **Import Users** > **Import from another tenant**
- Select source tenant
- Choose users to import
- Map to target groups (optional)
- Click **Import**

**From JSON file**:

- Click **Import Users** > **JSON File Import**
- Upload JSON file with user data
- Review import preview
- Click **Import**

**JSON format example**:

```json
[
  {
    "email": "user@example.com",
    "givenName": "John",
    "familyName": "Doe",
    "username": "johndoe",
    "identityIssuer": "https://auth.example.com"
  }
]
```

**Security recommendations**:

- Validate imported data before confirming import
- Review user assignments after bulk operations
- Use group-based access control rather than individual assignments
- Audit user creation events regularly

---

### User Groups

**Location**: Users and Groups > User Groups

**Purpose**: Organize users into groups for simplified access control and role management.

#### What you can do

- **View all groups**: Table displays group name, description, namespace, user count, role counts, privilege status
- **Create groups**: Define new user groups
- **Edit groups**: Update group properties, manage members, assign roles/permissions
- **Delete groups**: Remove unused groups
- **Download users**: Export list of group members
- **Import users**: Bulk add users to group from JSON

#### Group Properties

Groups display the following information in the table:

- **Name**: Unique group identifier
- **Description**: Purpose or scope of the group
- **Namespace**: Logical isolation boundary
- **Users**: Number of members
- **Access Roles**: Number of Access Roles assigned (some UI labels still say **API Roles**)
- **isPrivileged**: Whether this is a privileged group (elevated permissions)

#### Creating a User Group

- Click **+ ADD GROUP**
- Complete the group creation dialog (see [User Group Management Reference](#user-group-management-reference))
- Click **Save**

#### Group Operations

- **Settings**: Click settings icon to edit group and manage members/roles
- **Download Users**: Export member list for this group
- **Import Users**: Bulk add users from JSON file
- **Delete**: Remove group (does not delete member users)

**Use cases**:

- **Department groups**: "engineering", "sales", "marketing"
- **Role groups**: "developers", "managers", "admins"
- **Project groups**: "project-alpha-team", "project-beta-viewers"
- **Permission groups**: "read-only-users", "power-users"

**Best practices**:

- Use descriptive group names that reflect purpose
- Document group purpose in description field
- Prefer group-based access over individual user assignments
- Review group membership quarterly
- Use namespaces to isolate groups across environments/projects

---

### Assigned Roles

**Location**: Users and Groups > Assigned Roles

**Purpose**: View and manage all role assignments (user-to-role mappings) across the tenant.

**Availability**: Tenant admins and super admins only (hidden from regular users).

#### What you can see

- **Global view**: All role assignments in one place
- **User-role mappings**: Which users have which roles
- **Role utilization**: How roles are being used
- **Group-role relationships**: Roles assigned via group membership

#### Use cases

- **Access audit**: Review who has which roles
- **Compliance reporting**: Document role assignments for auditors
- **Cleanup**: Identify and remove unused role assignments
- **Troubleshooting**: Verify user has expected role access

**Security note**: This is a read-only view for auditing purposes. To modify role assignments, edit the user or group directly.

---

### Assigned Permissions

**Location**: Users and Groups > Assigned Permissions

**Purpose**: View and manage all permission assignments (user-to-permission mappings) across the tenant.

**Availability**: Tenant admins and super admins only (hidden from regular users).

#### What you can see

- **Global view**: All permission assignments in one place
- **User-permission mappings**: Which users have which permissions
- **Permission utilization**: How permissions are being used
- **Direct vs. role-based**: Permissions assigned directly vs. through roles

#### Use cases

- **Permission audit**: Review who has access to what
- **Least privilege verification**: Ensure users don't have excessive permissions
- **Compliance reporting**: Document permission grants
- **Troubleshooting**: Verify user has expected permissions

**Security note**: This is a read-only view for auditing purposes. To modify permissions, edit the user or group directly, or adjust role definitions.

**Best practice**: Prefer role-based permission assignment over direct grants. Direct permissions should be exceptions, not the norm.

---

## User Management Reference

When creating or editing a user via **Users > Settings icon**, you'll encounter a multi-tab configuration dialog.

### Creating a User

**Prerequisites**:

- At least one identity provider must be configured
- Appropriate permissions: `auth.users.create` or tenant admin

**Process**:

- Click **+ ADD USER** from Users dashboard
- Fill in user details across tabs
- Click **Save**

### User Settings Tab

**Purpose**: Core user profile information and authentication settings.

#### Identity Configuration

- **Identity Issuer\*** (required)
  - Select the identity provider this user will authenticate with
  - Dropdown lists all configured IdPs
  - **Note**: Cannot be changed after user creation
  - **Format**: Displays provider name and issuer URL

- **Email\*** (required)
  - User's primary email address
  - Must be unique within tenant+issuer combination
  - **Autocomplete**: As you type, suggests existing users from other tenants
  - **Validation**: Must be valid email format

- **Alternative Email**
  - Secondary email for notifications
  - Optional field
  - Must be valid email format if provided

- **IAL** (Identity Assurance Level)
  - Read-only field
  - Indicates identity verification level from IdP
  - Values: IAL1, IAL2, IAL3 (higher = stronger verification)

#### Profile Information

- **Username**
  - Unique identifier for the user
  - Optional (defaults to email if not provided)
  - **Use case**: Display name different from email

- **First Name**
  - User's given name
  - Optional (may be populated from IdP)

- **First Name Override**
  - Override the IdP-provided first name
  - **Warning**: May be ignored based on tenant provider settings
  - **Use case**: Correct IdP data without changing source

- **Last Name**
  - User's family name
  - Optional (may be populated from IdP)

- **Last Name Override**
  - Override the IdP-provided last name
  - **Warning**: May be ignored based on tenant provider settings

- **Name**
  - Full display name
  - Optional (may be computed from first + last name)

- **Name Override**
  - Override the IdP-provided full name
  - **Warning**: May be ignored based on tenant provider settings

- **Nickname**
  - Informal or preferred name
  - Optional

- **Nickname Override**
  - Override the IdP-provided nickname
  - **Warning**: May be ignored based on tenant provider settings

**Note on Overrides**: Overrides allow you to correct or customize user data without modifying the identity provider. However, if the IdP is configured to always use its data, overrides may be ignored at authentication time.

#### License Assignment (Super Admin Only)

- **License Type**
  - Assign a specific license to this user
  - Dropdown lists available licenses
  - **Restriction**: Only super admins can modify
  - **Use case**: Allocate user to specific license quota

#### User Expiration

- **Expire User Access on Date**
  - Set an expiration date for user access
  - Toggle to enable/disable expiration
  - **Date Picker**: Select expiration date and time
  - **Use case**: Temporary access, contractor accounts, trial periods
  - **Behavior**: User cannot authenticate after expiration

**Security recommendations**:

- Set expiration dates for temporary access
- Use alternative email for account recovery
- Review overrides carefully (potential for confusion)
- Assign users to appropriate licenses for quota management

---

### Groups Tab

**Purpose**: Assign the user to user groups.

#### Managing Group Membership

- **Current Groups**: Table displays groups user is currently a member of
- **Add to Groups**:
  - Autocomplete search for groups
  - Click **Add** to assign user to group
  - User inherits all roles and permissions from the group
- **Remove from Groups**:
  - Click remove icon next to group name
  - User loses group-based roles and permissions

**Group columns**:

- **Name**: Group identifier
- **Description**: Group purpose
- **Namespace**: Logical boundary
- **Remove**: Remove user from group

**Best practice**: Use groups rather than individual role/permission assignments for easier management at scale.

---

### Roles Tab

**Purpose**: Assign application-level roles directly to the user.

#### Managing Role Assignments

- **Current Roles**: Table displays roles currently assigned to user
  - Shows roles assigned directly (not through groups)
- **Add Roles**:
  - Autocomplete search for available roles
  - Click **Add** to assign role
  - User gains all permissions associated with the role
- **Remove Roles**:
  - Click remove icon next to role name
  - User loses role's associated permissions

**Role columns**:

- **Name**: Role identifier
- **Description**: Role purpose
- **Client**: Application this role belongs to
- **Permissions**: Number of permissions in this role
- **Remove**: Unassign role from user

**Security note**: Direct role assignments should be exceptions. Prefer group-based role assignment for maintainability.

---

### Permissions Tab

**Purpose**: Assign individual permissions directly to the user.

#### Managing Permission Assignments

- **Current Permissions**: Table displays permissions currently assigned
  - Shows only directly assigned permissions (not from roles or groups)
- **Add Permissions**:
  - Autocomplete search for available permissions
  - Click **Add** to grant permission
  - User gains that specific access right
- **Remove Permissions**:
  - Click remove icon next to permission name
  - User loses that access right

**Permission columns**:

- **Name**: Permission identifier (e.g., "read:users")
- **Description**: Permission purpose
- **Scope**: OAuth 2.0 scope value
- **Remove**: Revoke permission from user

**Security warning**: Direct permission assignment should be rare. Prefer roles and groups for maintainability and auditability.

**Best practice**: Document justification when granting individual permissions (use description field or audit comment).

---

### Profile Data Tab

**Purpose**: View and manage additional user profile attributes.

#### Profile Attributes

- **Custom Attributes**: Tenant-defined user attributes (if configured)
- **IdP Claims**: Additional data from identity provider
- **Metadata**: System-generated user metadata

**Use cases**:

- Store department, employee ID, cost center
- Track user preferences or settings
- Store application-specific user data

**Security note**: Be cautious storing sensitive data in user profiles. Use appropriate encryption and access controls.

---

### TOTP Tab

**Purpose**: Manage Time-based One-Time Password (TOTP) multi-factor authentication for the user.

#### TOTP Management

- **View TOTP Status**: Whether user has TOTP configured
- **Reset TOTP**: Remove TOTP configuration (requires re-enrollment)
- **View TOTP Secret**: Display secret for manual configuration (super admin only)

**Operations**:

- **Reset TOTP**:
  - Removes user's TOTP configuration
  - Requires user to re-enroll on next login
  - **Use case**: Lost device, troubleshooting

**Security recommendations**:

- Only reset TOTP after verifying user identity
- Document TOTP resets in audit comments
- Encourage users to store backup codes
- Consider requiring re-verification after reset

---

### Passkeys Tab

**Purpose**: Manage WebAuthn/FIDO2 passkeys for phishing-resistant authentication.

#### Passkey Management

- **View Registered Passkeys**: Table of user's registered passkeys
- **Passkey Details**: Device name, registration date, last used
- **Remove Passkeys**: Revoke specific passkey

**Passkey columns**:

- **Credential Name**: User-assigned name (e.g., "iPhone", "YubiKey")
- **AAGUID**: Authenticator attestation GUID (device type identifier)
- **Registered**: Date passkey was added
- **Last Used**: Most recent authentication with this passkey
- **Remove**: Revoke this passkey

**Operations**:

- **Remove Passkey**: Click remove icon to revoke
  - User can no longer authenticate with that device
  - **Use case**: Lost device, security incident

**Security recommendations**:

- Encourage users to register multiple passkeys (backup devices)
- Remove passkeys for lost or stolen devices immediately
- Passkeys are phishing-resistant (prefer over TOTP when possible)
- Monitor for suspicious passkey registrations

---

### SSH Keys Tab

**Purpose**: Manage SSH public keys for terminal/CLI access.

**Availability**: Only visible if SSH access features are enabled.

#### SSH Key Management

- **View Registered Keys**: User's SSH public keys
- **Add SSH Key**: Register new public key
- **Remove SSH Key**: Revoke access

**Operations**:

- **Add SSH Key**:
  - Paste SSH public key (PEM format)
  - Assign descriptive name
  - Set expiration (optional)
- **Remove SSH Key**: Revoke access for that key

**Security recommendations**:

- Rotate SSH keys regularly (every 6-12 months)
- Remove keys for former employees immediately
- Use separate keys for different access levels
- Set expiration dates for temporary access
- Monitor SSH key usage in audit logs

---

## User Group Management Reference

When creating or editing a user group via **User Groups > Settings icon**, you'll encounter a multi-tab configuration dialog.

### Creating a User Group

**Prerequisites**:

- Appropriate permissions: `auth.groups.create` or tenant admin

**Process**:

- Click **+ ADD GROUP** from User Groups dashboard
- Fill in group details across tabs
- Click **Save**

### Group Settings Tab

**Purpose**: Core group properties and configuration.

#### Group Identity

- **Name\*** (required)
  - Unique identifier for the group
  - Maximum 50 characters
  - Must contain at least one letter
  - **Naming convention**: Use descriptive names (e.g., "engineering-team", "read-only-users")

- **Description**
  - Detailed explanation of group purpose
  - Maximum 200 characters
  - **Best practice**: Document who should be in this group and what access it grants

#### Group Properties

- **Is Privileged**
  - Toggle to mark group as privileged
  - Privileged groups may have elevated permissions or bypass certain restrictions
  - **Security warning**: Use sparingly; reserved for admin or elevated-access groups
  - **Use case**: Admin groups, operator groups, elevated support access

- **Allow Membership Extension Requests**
  - Toggle to enable users to request extended membership
  - When enabled, users can request to extend their group membership
  - **Use case**: Time-limited project teams, temporary access groups

- **Namespace**
  - Logical isolation boundary for the group
  - Selector dropdown lists available namespaces
  - **Required**: Must select a namespace (no null option)
  - **Use case**: Separate groups by environment (prod/dev) or department

**Security recommendations**:

- Use clear naming conventions for groups
- Document group purpose in description
- Only mark groups as privileged when necessary
- Use namespaces to enforce isolation

---

### Members Tab

**Purpose**: Manage user membership in the group.

#### Member Management

- **Current Members**: Paginated table of group members
  - Displays name, email, issuer, member since
  - Supports filtering by email
  - Lazy loading for large groups (handles thousands of members)

- **Add Members**:
  - Autocomplete search for users
  - Type email to search across all tenant users
  - Displays email and issuer for each match
  - Click **Add** to assign user to group
  - Users immediately inherit group's roles and permissions

- **Remove Members**:
  - Click remove icon next to user
  - User loses group-based roles and permissions
  - Does not delete the user account

**Member table columns**:

- **Name**: User's full name
- **Email**: User's email address
- **Identity Issuer**: Authentication provider
- **Member Since**: Date added to group
- **Expiration**: Membership expiration date (if set)
- **Remove**: Remove from group

#### Filtering and Search

- **Filter by Email**: Real-time search across members
- **Pagination**: Configurable rows per page (10-1000)
- **Sort**: Click column headers to sort

**Best practices**:

- Review group membership quarterly
- Remove users who no longer need access
- Use descriptive search to find members quickly
- Set expiration dates for temporary access

---

### Access Roles Tab

**Purpose**: Assign **Access Roles** (also called **API Roles** in some UI labels) to all group members. Access Roles are scoped to a resource server — either a standalone API resource server or a client-linked / auto-generated resource server associated with an OAuth client.

#### Managing Access Roles

- **Current Access Roles**: Table of Access Roles assigned to this group
- **Add Access Roles**:
  - Autocomplete search for available Access Roles
  - Select role and click **Add**
  - All group members inherit this role
- **Remove Access Roles**:
  - Click remove icon next to role
  - All group members lose this role

**Role columns**:

- **Role Name**: Role identifier
- **Resource Server / API**: Authorization context for the role
- **Description**: Role purpose
- **Permissions**: Number of Resource Server Permissions in the role
- **Remove**: Unassign role from group

**Use case**: Grant an "order-manager" Access Role for the Order Management API to the entire "order-managers" group.

**Security benefit**: When you add a user to the group, they automatically get all group Access Roles without additional configuration.

> **Compatibility**: Deprecated group `/roles` relation routes and flat role claims are documented in [Roles and Permissions Compatibility](../authorization/legacy-roles-permissions-compatibility.md).

---

### UMRS Grants Tab

**Purpose**: Assign User-Managed Role System (UMRS) grants to the group.

**Note**: UMRS is an advanced feature for delegated role management. This tab may not be visible if UMRS is not configured for your tenant.

#### What is UMRS?

UMRS (User-Managed Role System) allows designated users to grant/revoke specific resource-server-scoped Access Roles without being full admins. It's useful for:

- Project leads managing project access
- Team managers controlling team resources
- Delegated administration

#### Managing UMRS Grants

- **Current UMRS Grants**: Roles this group can grant to others
- **Add UMRS Grant**:
  - Select UMRS role
  - Grant permission to assign that role
  - Group members can now grant this role to other users
- **Remove UMRS Grant**:
  - Revoke delegation permission
  - Group members can no longer grant that role

**Security warning**: UMRS grants are powerful. Only assign to trusted groups/users who need delegation capabilities.

---

## Security Best Practices

### User Management Security

- **Follow least privilege**
    - Grant minimum permissions required for job function
    - Review and revoke unused permissions regularly
    - Use time-limited access for temporary needs

- **Use group-based access control**
    - Assign permissions to groups, not individual users
    - Easier to audit and maintain
    - Reduces error-prone individual assignments

- **Implement strong authentication**
    - Prefer passkeys (WebAuthn) over TOTP when possible
    - Require MFA for admin and privileged groups
    - Enforce MFA for sensitive applications

- **Set appropriate expirations**
    - Use user expiration for contractors, temps, trials
    - Set group membership expiration for project-based access
    - Monitor and clean up expired accounts regularly

- **Audit access regularly**
    - Review user roles and permissions quarterly
    - Check group memberships for accuracy
    - Use Assigned Roles/Permissions views for audits
    - Remove unused accounts promptly

### Group Management Security

- **Define clear group purposes**
    - Document what each group is for
    - Use descriptive names
    - Avoid overlapping group responsibilities

- **Limit privileged groups**
    - Only mark groups as privileged when truly necessary
    - Audit privileged group membership frequently
    - Require justification for privileged group access

- **Use namespaces for isolation**
    - Separate production from non-production
    - Isolate departments or projects
    - Enforce namespace boundaries strictly

- **Control group membership**
    - Require approvals for sensitive group additions
    - Monitor group membership changes in audit logs
    - Set up alerts for privileged group modifications

- **Review role assignments**
    - Ensure groups have only necessary roles
    - Remove unused role assignments
    - Document why each role is assigned

### Administrative Best Practices

- **Separation of duties**
    - Don't use your admin account for daily tasks
    - Have separate admin and user accounts
    - Require multiple approvals for sensitive changes

- **Change management**
    - Test permission changes in non-production first
    - Document all privilege escalations
    - Have rollback plans for group/role changes

- **Monitoring and alerting**
    - Alert on privileged group membership changes
    - Monitor failed authentication attempts
    - Track bulk user imports/deletions
    - Review event logs for suspicious patterns

- **Data protection**
    - Don't store sensitive data in user profiles
    - Encrypt PII appropriately
   - Comply with data retention policies
   - Have user data export/deletion procedures (GDPR)

- **Access reviews**
    - Quarterly access reviews for all users
    - Immediate review when users change roles
   - Annual comprehensive privilege audit
   - Remove access for terminated employees immediately

---

## Additional Resources

- [License Management Guide](license-management-guide.md) (Super Administrators)
- [Tenant Administrator Guide](tenant-admin-guide.md)
- [SSO Integration Guide](sso-integration-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)
- **Authifi API Documentation**: Available via Help > API Documentation in the UI

---

## Getting Help

For assistance with user and group management:

- **Check event logs** for authentication/authorization failures
- **Review Assigned Roles/Permissions** views for access issues
- **Test in non-production** before making bulk changes
- **Contact super administrator** for license or elevated privilege requests
- **Consult audit logs** for historical changes and troubleshooting

---

**Document version**: 1.1  
**Last updated**: 2025-12-12  
**Target audience**: Tenant Administrators  
**Note**: System administrators should also refer to the License Management Guide

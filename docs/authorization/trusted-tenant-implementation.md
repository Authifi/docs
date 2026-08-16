# Trusted Tenant Relationships

## Overview

The Trusted Tenant feature enables cross-tenant resource management without requiring separate authentication tokens or admin group membership for each tenant. Tenant administrators can delegate management privileges to other tenants through configurable trust relationships.

## Use Cases

Use trusted tenant relationships for:

- **Centralized administration**: A central IT team manages multiple organizational tenants from one admin account.
- **Managed service providers**: External administrators manage client tenants without separate credentials.
- **Hierarchical organizations**: Parent organizations oversee subsidiary tenants while maintaining separation.
- **Shared services**: A shared services tenant manages resources across multiple business units.

## How It Works

When trust is established:

1. **Tenant B trusts Tenant A**: Administrators of Tenant A can manage Tenant B's resources
2. **Single Authentication**: Admins use their Tenant A credentials—no re-authentication needed
3. **Full Access**: Trusted admins have the same capabilities as native tenant admins

### Trust Relationship Direction

Trust relationships are **unidirectional**:

- If Tenant B trusts Tenant A, then Tenant A admins can manage Tenant B
- Tenant B admins cannot manage Tenant A.
- To enable bidirectional management, create trust relationships in both directions

## Managing Trust Relationships

### API Endpoints

All endpoints require tenant admin privileges and are under `/auth/admin/tenants/{tenantId}`:

| Method   | Endpoint                          | Description                          |
| -------- | --------------------------------- | ------------------------------------ |
| `PUT`    | `/trust-tenant/{trustedTenantId}` | Establish trust relationship         |
| `DELETE` | `/trust-tenant/{trustedTenantId}` | Remove trust relationship            |
| `GET`    | `/manages-tenants`                | List tenants managed by this tenant  |
| `GET`    | `/managed-by-tenants`             | List tenants that manage this tenant |

### Examples

**Establish trust** (allow Tenant A admins to manage Tenant B):

```http
PUT /auth/admin/tenants/B/trust-tenant/A
Authorization: Bearer <tenant-admin-token>
```

**Remove trust relationship**:

```http
DELETE /auth/admin/tenants/B/trust-tenant/A
Authorization: Bearer <tenant-admin-token>
```

**List all tenants that Tenant A can manage**:

```http
GET /auth/admin/tenants/A/manages-tenants
Authorization: Bearer <token>
```

**List all tenants that can manage Tenant B**:

```http
GET /auth/admin/tenants/B/managed-by-tenants
Authorization: Bearer <token>
```

### Using the Authifi Admin UI

1. Log in to the Authifi Admin UI as a tenant administrator
2. Navigate to the target tenant's settings
3. Select **Trusted Tenants** from the menu
4. Add or remove trusted tenant relationships as needed

## Security Considerations

### Access Requirements

- **Admin Privileges Required**: Only tenant administrators can establish or remove trust relationships
- **Identity Provider Validation**: Users must authenticate via trusted identity providers
- **Audit Logging**: All cross-tenant access is logged with full context for compliance

### Best Practices

1. Grant trust only to tenants that require cross-tenant management.
2. Audit trust relationships regularly and remove those no longer needed.
3. Record why each trust relationship exists.
4. Use audit logs to track cross-tenant administrative actions.

## Limitations

1. **Client Credentials Exclusion**: Service-to-service authentication (client credentials flow) cannot use trusted tenant features—only user-based authentication is supported
2. **Admin-Only Access**: The feature is limited to users with tenant administrator privileges
3. **Unidirectional Trust**: Each trust relationship is one-way; bidirectional access requires two separate relationships
4. **Partial Endpoint Coverage**: Some Authifi endpoints may not recognize trusted tenant relationships

## Configuration

The Trusted Tenant feature is enabled by default, requires no environment variables or setting changes, and uses the existing authentication infrastructure.

## Related Documentation

- [Delegating Tenant Management to a Shared Tenant](delegating-tenant-management-to-a-shared-tenant.md) - Step-by-step guide for setting up delegation
- [Authorization](authorization.md) - General authorization concepts
- [Admin Roles](admin-roles.md) - Understanding administrator privileges

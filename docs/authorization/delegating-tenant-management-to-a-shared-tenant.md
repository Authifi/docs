## Delegating Authorization to a Shared Tenant

Each Authifi tenant has an "admins" group that grants elevated privileges to each member. The elevated privileges allow management of the tenant's resources without requiring an access token with scope(s) for each API operation (for example: "auth.clients.list"). In addition,
if a user belongs to the "admins" group across multiple tenants, they can manage the resources from any of those tenants without requiring a new access token. Tenant creators are automatically added to the "admins" group and can add new members.

To avoid assigning users to each tenant's "admins" group for cross-tenant access, Authifi supports "delegating" access to a shared tenant via "Trusted Tenant" assignment.

### Steps

- First, your account must be a member of the "admins" group for the tenant(s) you wish to delegate access to.
- Next, establish a trusted tenant relationship with one or more different Tenant you want to delegate management privileges to. REST API documentation for managing trusted tenants can be found in the Trusted Tenant API documentation.
- After assigning the Trusted Tenants, members of the "admins" group of the shared Tenant can manage the resources of any of the Trusted Tenant(s) without requiring a new access token or membership in their respective "admins" group.

### Known Limitations

- Some of the Authifi endpoints check for tenant administrator privileges in the request and might not work even if the trusted tenant relationship is established.
- Client Credentials Grant tokens are not supported by the Trusted Tenant feature.

### Technical Implementation

For detailed technical documentation including architecture, code examples, and system integration details, see the [Trusted Tenant Feature - Technical Implementation Guide](trusted-tenant-implementation.md).

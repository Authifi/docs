## Delegating Authorization to a Shared Tenant

Each Authifi tenant has an "admins" group. Members can manage that tenant's resources without an access token containing scopes for every API operation, such as auth.clients.list. Tenant creators join this group automatically and can add members. A user who belongs to the "admins" group in multiple tenants can manage resources in all of them without requesting a new access token.

To avoid assigning users to every tenant's "admins" group, Authifi supports delegating access through a "Trusted Tenant" assignment.

### Steps

- Your account must be a member of the "admins" group for each tenant you want to delegate from.
- Establish a trusted tenant relationship with each tenant you want the shared tenant to manage. See the Trusted Tenant API documentation for the REST API.
- After the assignment, members of the shared tenant's "admins" group can manage resources in each trusted tenant without a new access token or membership in those tenants' "admins" groups.

### Known Limitations

- Some Authifi endpoints check for tenant administrator privileges in the request and may not work after the trusted tenant relationship is established.
- Client Credentials Grant tokens are not supported by the Trusted Tenant feature.

### Technical Implementation

See the [Trusted Tenant Feature - Technical Implementation Guide](trusted-tenant-implementation.md) for architecture, code examples, and integration details.

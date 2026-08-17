# Authifi Documentation

Authifi is an enterprise identity broker, access management, and application security platform.

## Getting Started

### Authorization

Authifi supports admin roles, RBAC, OAuth scopes, custom roles and permissions, scripted authorization rules, and fine-grained access control.

- [OAuth Client Authorization](authorization/authorization.md) - User groups, API authorization, and RBAC
- [Admin Roles](authorization/admin-roles.md) - System admin, tenant admin, and delegated admin roles
- [Privileged Access Summary](authorization/privileged-access-summary.md) - Privileged permissions, roles, and groups
- [Super Admin Access](authorization/super-admin-access.md) - Super administrator requirements and capabilities
- [Default Application User Groups](authorization/default-application-user-groups.md) - Automatically assign users to groups after login
- [Delegating Tenant Management](authorization/delegating-tenant-management-to-a-shared-tenant.md) - Cross-tenant access via trusted tenants
- [Trusted Tenant Implementation](authorization/trusted-tenant-implementation.md) - Configure cross-tenant management relationships

### Administrator Guides

Guides for tenant administrators:

- [Tenant Administration](guides/tenant-admin-guide.md) - Configure tenant settings, branding, and security
- [Users and Groups](guides/users-groups-admin-guide.md) - Manage users, groups, roles, and permissions
- [SSO Integration](guides/sso-integration-guide.md) - Configure applications and identity providers
- [Access Requests](guides/access-requests-guide.md) - Self-service access and delegated administration
- [License Management](guides/license-management-guide.md) - Manage platform licenses (super admins)
- [Monitoring and Logging](guides/monitoring-guide.md) - Audit logs, event logs, and security monitoring
- [NHE Delegated Tokens](guides/nhe-delegated-tokens.md) - Short-lived tokens for LLM agents and automated pipelines
- [Resources and Tools](guides/resources-tools-guide.md) - Templates, images, secrets, and scheduled jobs

### Security

Security configuration:

- [Security Overview](security/README.md) - Security documentation index
- [Security Admin Guide](security/security-admin-guide.md) - Administrative account lifecycle and security
- [Recommended Secure Configuration](security/recommended-secure-configuration.md) - FedRAMP-aligned security guidance

## Feature Overview

See the [Authifi Identity Broker Feature List](feature-list.html) for the full capability list: authentication protocols, MFA, RBAC, secret management, AI agent delegation, GA4GH Passport support, and FedRAMP High compliance.

## FedRAMP

Authifi is FedRAMP High authorized as a supporting service in the [Palantir Federal Cloud Service (PFCS-SS)](https://marketplace.fedramp.gov/products/FR2315464863).

- [FedRAMP compliance evidence](security/fedramp-compliance-evidence.md)

## Support

For questions or issues, contact your Authifi administrator or support team.

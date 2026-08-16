# Roles and Permissions Compatibility

Current integrations use one authorization model:

- **Access Roles** (`AccessRole`) group permissions for an authorization context. Some UI screens still label these **API Roles**.
- **Resource Server Permissions** (`ResourceServerPermission`) represent fine-grained permissions.
- `/access-roles` manages Access Roles.
- `/resource-servers/{resourceServerId}/permissions` manages permissions.
- Ordinary API access tokens carry granted Resource Server Permissions in the `scope` claim.
- Trusted UserInfo and server-side session claims can carry `resource_roles`, keyed by resource server identifier.

An OAuth client can have a linked or auto-generated resource server, so application-assigned authorization and standalone API authorization use the same Access Role and Resource Server Permission entities.

**Client permissions** are Resource Server Permissions on an OAuth client's auto-generated placeholder resource server.

**Client credential roles and permissions** mean Access Roles marked for client credential grants, their Resource Server Permissions, and the associated client. This authorization is assigned to applications rather than users.

For configuration guidance, see the [SSO Integration Guide](../guides/sso-integration-guide.md) and [OIDC Request Scopes](../guides/oidc-request-scopes.md).

All routes below are relative to `/auth/admin/tenants/{tenantId}` unless the full path is shown.

## Deprecated role route family

The `/roles` controller is a deprecated wrapper over `AccessRole` that maps legacy role shapes to the unified store. `POST /roles` requires a client and creates the Access Role on that client's linked or auto-generated resource server. The wrapper can also return tenant roles outside the application context:

- `GET /roles` returns tenant Access Roles across client-linked and standalone resource servers unless the caller supplies a `clientId` filter.
- `GET /roles/count` and the read, update, replace, and delete routes keyed by role ID constrain by tenant, but do not require the Access Role to be client-associated.
- Role-permission relation routes also operate on the unified tenant `AccessRole` and `ResourceServerPermission` records while returning legacy shapes.

Use the canonical routes for new integrations so the resource-server context is explicit. Do not rely on `/roles` to enforce an application-only boundary.

| Deprecated route                                        | Canonical replacement                                                                                    |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `POST /roles`                                           | `POST /access-roles`                                                                                     |
| `GET /roles`                                            | `GET /access-roles`                                                                                      |
| `GET /roles/count`                                      | `GET /access-roles/count`                                                                                |
| `GET /roles/{id}`                                       | `GET /access-roles/{id}`                                                                                 |
| `PATCH /roles/{id}`                                     | `PATCH /access-roles/{id}`                                                                               |
| `PUT /roles/{id}`                                       | `PUT /access-roles/{id}`                                                                                 |
| `DELETE /roles/{id}`                                    | `DELETE /access-roles/{id}`                                                                              |
| `GET /roles/{roleId}/permissions`                       | `GET /access-roles/{accessRoleId}/resource-server-permissions`                                           |
| `PUT /roles/{roleId}/permissions/rel/{permissionId}`    | `PUT /resource-servers/{resourceServerId}/permissions/{permissionId}/access-roles/rel/{accessRoleId}`    |
| `DELETE /roles/{roleId}/permissions/rel/{permissionId}` | `DELETE /resource-servers/{resourceServerId}/permissions/{permissionId}/access-roles/rel/{accessRoleId}` |

The relation replacements require the Resource Server Permission's `resourceServerId`; the legacy route inferred the application resource context.

## Deprecated permission route family

The `/permissions` controller is a deprecated wrapper over `ResourceServerPermission`. It resolves the legacy application context to the client's resource server:

| Deprecated route           | Canonical replacement                                                                      |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| `POST /permissions`        | `POST /resource-servers/{resourceServerId}/permissions`                                    |
| `GET /permissions`         | `GET /resource-servers/{resourceServerId}/permissions`                                     |
| `GET /permissions/count`   | `GET /resource-server-permissions/count` with an appropriate filter                        |
| `GET /permissions/{id}`    | `GET /resource-servers/{resourceServerId}/permissions/{permissionId}`                      |
| `PATCH /permissions/{id}`  | `PATCH /resource-servers/{resourceServerId}/permissions/{permissionId}`                    |
| `PUT /permissions/{id}`    | `PATCH /resource-servers/{resourceServerId}/permissions/{permissionId}`                    |
| `DELETE /permissions/{id}` | `DELETE /resource-servers/{resourceServerId}/permissions/{permissionId}`                   |
| `DELETE /permissions`      | No canonical bulk-delete route; list the resource server permissions and delete each by ID |

## Deprecated relation and legacy views

These routes expose legacy application-role names or shapes over the unified stores:

| Deprecated route                                             | Canonical replacement                                            |
| ------------------------------------------------------------ | ---------------------------------------------------------------- |
| `PUT /groups/{groupId}/roles/rel/{roleId}`                   | `PUT /groups/{groupId}/access-roles/rel/{accessRoleId}`          |
| `DELETE /groups/{groupId}/roles/rel/{roleId}`                | `DELETE /groups/{groupId}/access-roles/rel/{accessRoleId}`       |
| `GET /groups/{groupId}/roles`                                | `GET /groups/{groupId}/access-roles`                             |
| `GET /clients/{id}/roles`                                    | `GET /access-roles` filtered by `clientId`                       |
| `GET /users/{userId}/permissions`                            | `GET /users/{userId}/resource-server-permissions`                |
| `GET /users/{userId}/roles`                                  | Group-inherited Access Roles: get the user's groups, then `GET /groups/{groupId}/access-roles`. Direct user-role assignments have no separate canonical list endpoint; prefer group-assigned Access Roles for new integrations, or retain the deprecated route until those assignments are migrated. |
| `GET /users/{userId}/app-and-api-roles`                      | Get group Access Roles, then their Resource Server Permissions   |
| `POST /client/{clientId}/users-with-set-of-app-permissions`  | No resource-scoped canonical set-query; see below                |
| `GET /users-with-app-permissions/{permissionId}`             | `GET /users-with-api-permissions/{permissionId}`                 |
| `GET /users/count-users-with-app-permissions/{permissionId}` | `GET /users/count-users-with-api-permissions/{permissionId}`     |
| `GET /users-with-app-roles/{roleId}`                         | `GET /users-with-api-roles/{roleId}`                             |
| `GET /users/count-users-with-app-roles/{roleId}`             | `GET /users/count-users-with-api-roles/{roleId}`                 |

Do not use the deprecated wrappers or flat claims for new integrations.

The deprecated `POST /client/{clientId}/users-with-set-of-app-permissions` route is not equivalent to `POST /users-with-set-of-api-permissions`. The deprecated route constrains Access Roles by `clientId`. The canonical API permission-name query aggregates matches across the tenant and has no resource server filter. Because permission names are unique only within a resource server, it can combine same-named permissions from different APIs.

Until a resource-scoped set query is available, existing consumers that need the exact client-specific result may retain the deprecated route. New integrations should resolve the client's linked Resource Server, resolve every permission by exact name within that Resource Server, list Access Roles for that resource context, and traverse their group and user assignments. Include a user only when that one resource context grants the complete requested permission set. Do not substitute the tenant-wide permission-name query.

## Deprecated claim compatibility

The `enableLegacyRoles` client feature toggle enables flat `roles` and `access_roles` claims for older consumers. These claims discard the resource server boundary and can collide when resources use the same role name.

For normal API authorization, validate the access token and enforce the required permission from its space-separated `scope` claim. `groups` and `resource_roles` are not ordinary access-token claims. Only use role or group checks when a trusted UserInfo or server-side session flow supplies those claims.

To migrate a trusted UserInfo or session role consumer:

1. Remove `access_roles` from the requested OIDC scopes. It is not a separate request scope.
2. Request `profile`.
3. Read `resource_roles` from the trusted UserInfo or server-side session claims.
4. Select roles using the resource server identifier as the object key.

For example, replace a flat claim check such as:

```json
{
  "access_roles": ["reader"]
}
```

with a resource-scoped check:

```json
{
  "resource_roles": {
    "https://api.example.com": ["reader"]
  }
}
```

Code should read `resource_roles["https://api.example.com"]` and test that array for `reader`. This preserves the resource server boundary and avoids collisions between roles with the same name.

# Authifi OAuth Client Authorization

Authifi acts as an [authorization server](https://tools.ietf.org/html/rfc6749#section-1.1) for [OAuth clients](https://tools.ietf.org/html/rfc6749#section-2), [resource servers](https://tools.ietf.org/html/rfc6749#section-1.1) (APIs), and registered `Users`.

### User Groups and AD Groups

Each Authifi `Tenant` can define `User Groups` and assign or remove `Users` and `OAuth2 Clients` from them. Assigning a `User Group` to an `OAuth Client` restricts access to the assigned `OAuth Client`'s `User(s)` within the assigned `User Group(s)` for the `Client(s)`.

#### How to restrict Client access with User Groups configured via the Authifi UI

Initial steps

- Log in to the Authifi UI.
- View or create a `Tenant`.

##### Adding Users to a Tenant

`Users` assigned to an Authifi `Tenant` can be added to the `Tenant`'s `User Groups`.

- Go to the `Users` dashboard via the left navigation menu
- Click the `Add User` button
- Add `Users` to the `Tenant` by email address

_Note: New `Users` show only an email address in the `Users` dashboard until they log in for the first time. Authifi brokers identities from third parties such as Google instead of storing them in its own database._

##### Creating a User Group

- Go to the `User Groups` dashboard via the left navigation menu
- Click the `Add Group` button
- Specify the group name and description. You can assign existing users before creating the group.

##### Assigning/removing User Groups to OAuth Clients

- Go to the `Applications Dashboard` via the left navigation menu
- Select an existing `OAuth Client`
- In the edit dialog for the `OAuth Client`, go to the `Groups` tab
- Assign or remove `User Groups` for the `OAuth Client`.

#### How to restrict OAuth Client access by AD Group membership

Assign one or more Active Directory groups to `OAuth Clients` to restrict access to group members.
Only some identity providers return AD group information. The `Google OAuth` and `Azure OIDC` identity providers do not.

##### Assigning AD Groups to SAML2 OAuth Clients

- Go to the `Applications Dashboard` via the left navigation menu
- Select an existing `saml2` application or create a new one
- In the configuration editor, provide the `adGroups` attribute with a list of one or more AD Groups.

Example

```json
{
  "adGroups": ["My AD Group", "My Second AD Group"]
}
```

##### Assigning AD Groups to Web/Native OAuth Clients

- Go to the `Applications Dashboard` via the left navigation menu
- Select an existing `web` or `native` application or create a new one
- In the configuration editor, enter one or more AD Groups in the AD Group field, separated by commas or newlines.

### API Authorization

#### Registering an API (Resource Server)

- Go to the `APIs Dashboard` via the left navigation menu
- Click the "Add API" button
- Specify the `name` and unique `identifier` (the `API` audience).
- Optionally authorize `Clients` to access the `API`. The list of `Clients` authorized to access the `API` can be changed after the `API` is registered.

#### Resource Server Client Grants

Authorize `OAuth Clients` to access a specific `API` (Resource Server).

- Go to the `APIs Dashboard` via the left navigation menu
- Select an existing `API`
- In the edit dialog for the `API`, go to the `Clients` tab
- Assign/remove `Clients` to the `API`

### Role Based Access Control

An **Access Role** (also labeled an **API Role** in some UI screens) groups **Resource Server Permissions** within a resource-server authorization context. Assign Access Roles to `User Groups`. Resource Server Permissions provide granular access to a resource server, such as `facility.users.createFacility`. Granted permissions appear in API access tokens in the `scope` claim; APIs should verify that claim.

Use Access Roles to reuse permission mappings and assign them to `User Groups`. Client-linked, including auto-generated, resource servers and standalone API resource servers use the same Access Role and Resource Server Permission model.

For application role checks from trusted UserInfo or session data, request `profile` and read `resource_roles`, keyed by resource server identifier. See [Roles and Permissions Compatibility](./legacy-roles-permissions-compatibility.md) for deprecated flat claims and legacy admin routes.

### Identity Assurance Levels

Use the `acr_values` query parameter to request additional identity assurance during authentication. Authifi currently supports this ACR value:

| ACR Value                                              | Short Name | Details                               |
| :----------------------------------------------------- | :--------: | :------------------------------------ |
| http://schemas.openid.net/policies/modrna/multi-factor |   mod-mf   | Requests multi-factor authentication. |

The supported `acr_values` are listed in each tenant's OIDC Discovery URL under `acr_values_supported`.

## Example

`/_api/auth/<tenant>/authorize?acr_values=mod-mf&...`

With `mod-mf`, the user is prompted for multi-factor authentication even if the application or identity provider does not require it. Use this value for more sensitive application areas.

If the user is already authenticated, add the "prompt" query parameter with the value "login" to request MFA. See the [OIDC Specification](https://openid.net/specs/openid-connect-core-1_0.html) for details. Example: `/_api/auth/<tenant>/authorize?acr_values=mod-mf&prompt=login...`.

After MFA succeeds, the user's `id_token` and profile contain AMR claims that identify the authentication method. See the [AMR specification](https://tools.ietf.org/html/rfc8176).

| AMR Value | Details                                          |
| :-------- | :----------------------------------------------- |
| pwd       | Denotes username/password authentication.        |
| mfa       | Represents the use of MFA during authentication. |

For example, a user who authenticates with a username, password, and MFA receives an `amr` claim of `['pwd', 'mfa']`.

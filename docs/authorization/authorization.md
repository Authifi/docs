## Authifi OAuth Client Authorization

As an [Authorization Server](https://tools.ietf.org/html/rfc6749#section-1.1), Authifi provides layered authorization options for [OAuth Clients](https://tools.ietf.org/html/rfc6749#section-2), [Resource Servers](https://tools.ietf.org/html/rfc6749#section-1.1) (APIs), and `Users` registered to the system.

### User Groups and AD Groups

Each Authifi `Tenant` can define `User Groups` and assign/remove `Users` and/or `OAuth2 Clients` to each one. When a `User Group` is assigned to an `OAuth Client`, access to the `OAuth Client` will be restricted to the `User(s)` within the `User Group(s)` assigned to the `Client(s)`.

#### How to restrict Client access with User Groups configured via the Authifi UI

Initial steps

- Login to the Authifi UI
- View/create a `Tenant`

##### Adding Users to a Tenant

`Users` assigned to a Authifi `Tenant` can be added to the `Tenant`'s `User Groups`.

- Go to the `Users` dashboard via the left navigation menu
- Click the `Add User` button
- Add `Users` to the `Tenant` by email address

_Note: New `Users` will only display an email address in the `Users` dashboard columns until they login for the first time. This is due to Authifi brokering identities from third parties like Google rather than storing them in its own database._

##### Creating a User Group

- Go to the `User Groups` dashboard via the left navigation menu
- Click the `Add Group` button
- Specify the group name and description. Existing users can be assigned to the group before it is created.

##### Assigning/removing User Groups to OAuth Clients

- Go to the `Applications Dashboard` via the left navigation menu
- Select an existing `OAuth Client`
- In the edit dialog for the `OAuth Client`, go to the `Groups` tab
- Assign/remove `User Groups` to the `OAuth Client`

#### How to restrict OAuth Client access by AD Group membership

One or more Active Directory groups can be assigned to `OAuth Clients` to restrict access to members of the AD Group(s).
Currently, it is only possible to obtain AD Group information when logging in with specific Identity Providers. For example, the `Google OAuth` and `Azure OIDC` IdP does not provide AD Group information.

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
- In the configuration editor, fill in the AD Group text box a list of one or more AD Groups separated by commas or newlines.

### API Authorization

#### Registering an API (Resource Server)

- Go to the `APIs Dashboard` via the left navigation menu
- Click the "Add API" button
- Specify the `name` and unique `identifier` (the `API`'s audience)
- Optionally authorize `Clients` to access the `API`. The list of `Clients` authorized to access the `API` can be changed after the `API` is registered.

#### Resource Server Client Grants

Authorize `OAuth Clients` to access a specific `API` (Resource Server).

- Go to the `APIs Dashboard` via the left navigation menu
- Select an existing `API`
- In the edit dialog for the `API`, go to the `Clients` tab
- Assign/remove `Clients` to the `API`

### Role Based Access Control

A `Role` is a grouping of a set of permissions (Resource Scopes).
Roles can be assigned to one or more `User Groups` and each `Role` can be assigned one or more `Permissions`. `Permissions` define granular, `API`-level access control. For example: `facility.users.createFacility`. These will be populated in authenticated `User's` Bearer tokens under the `scope` claim. The `scope` claim can then be verified by the service receiving a request.

Use `Roles` to group low-level `Permissions` into reusable mappings. Similarly, use `User Groups` to group `Roles` into reusable mappings for a set of users.

### Identity Assurance Levels

When authorizing, the `acr_values` query parameter can be used to request additional levels of identity assurance when authenticating the user. Currently, Authifi supports the following ACR values:

| ACR Value                                              | Short Name | Details                               |
| :----------------------------------------------------- | :--------: | :------------------------------------ |
| http://schemas.openid.net/policies/modrna/multi-factor |   mod-mf   | Requests multi-factor authentication. |

The list of supported `acr_values` can also be viewed at the OIDC Discovery URL for each tenant under the `acr_values_supported` attribute.

## Example

`/_api/auth/<tenant>/authorize?acr_values=mod-mf&...`

When providing the `mod-mf` query parameter, the user will be prompted for multi-factor authentication even if the application or identity provider does not already require it. This adds another layer of security for more sensitive sections in an application.

Note:
If the user is already authenticated, use the "prompt" query parameter with a value of "login" to request MFA. For details on the "prompt" query parameter, refer to the [OIDC Specification](https://openid.net/specs/openid-connect-core-1_0.html). For example: `/_api/auth/<tenant>/authorize?acr_values=mod-mf&prompt=login...`.

After successfully authenticating with MFA, the user's `id_token` and user profile will contain additional claims specifying the type of authentication used (AMR values). See: [AMR specification](https://tools.ietf.org/html/rfc8176).

| AMR Value | Details                                          |
| :-------- | :----------------------------------------------- |
| pwd       | Denotes username/password authentication.        |
| mfa       | Represents the use of MFA during authentication. |

For example, if the user used both a username and password and MFA during authentication, then the `amr` claim would be `['pwd', 'mfa']`.

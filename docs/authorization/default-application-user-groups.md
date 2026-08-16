## Automatically Assigning Users to Groups after Login

When a user logs in, Authifi can automatically assign them to one or more **default application user groups** configured for an application. This removes the need to manually assign new users to groups after their first login.

### Steps

- Create a new application or select an existing application.
- Create one or more User Groups.
- Assign the User Groups to the application default user groups. This action can be completed in the **Default User Groups** tab of the Authifi UI or through the Client REST APIs.

When users login to the application, they will automatically become members of the configured groups. Assignment can be confirmed via the Authifi APIs or by visiting the **Groups** dashboard on the Authifi UI, selecting the group(s), and viewing users on the "Members" tab.

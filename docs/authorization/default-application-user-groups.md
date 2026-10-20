## Automatically Assigning Users to Groups after Login

When a user logs in, Authifi can add them to one or more **default application user groups** configured for an application. This removes the need to assign new users manually after their first login.

### Steps

- Create a new application or select an existing one.
- Create one or more User Groups.
- Assign the User Groups to the application's default user groups in the **Default User Groups** tab of the Authifi UI or through the Client REST APIs.

When users log in to the application, they become members of the configured groups. Confirm the assignment through the Authifi APIs or in the Authifi UI: open the **Groups** dashboard, select the group, and view its users on the **Members** tab.

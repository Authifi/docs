# License Management Guide (Super Admins)

Super Administrators manage license templates and workspace subscriptions through the Authifi UI.

## Table of Contents

- [License Management Guide (Super Admins)](#license-management-guide-super-admins)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Accessing License Management](#accessing-license-management)
  - [License Management Operations](#license-management-operations)
    - [Viewing License Templates](#viewing-license-templates)
    - [Creating a License Template](#creating-a-license-template)
    - [Editing a License Template](#editing-a-license-template)
    - [Deleting a License Template](#deleting-a-license-template)
  - [Workspace Licenses](#workspace-licenses)
  - [Sandbox Licenses](#sandbox-licenses)
    - [Requesting a Sandbox](#requesting-a-sandbox)
    - [Approving a Sandbox Request](#approving-a-sandbox-request)
    - [Extending, Upgrading, and Renewing](#extending-upgrading-and-renewing)
    - [Sandbox Expiration](#sandbox-expiration)
  - [License Properties Reference](#license-properties-reference)
    - [Name](#name)
    - [Slug](#slug)
    - [Term (days)](#term-days)
    - [Max clients / tenant](#max-clients-tenant)
    - [Max users / tenant](#max-users-tenant)
    - [Max child tenants](#max-child-tenants)
    - [Hourly login warning and hourly login limit](#hourly-login-warning-and-hourly-login-limit)
    - [Account holder](#account-holder)
  - [License Assignment](#license-assignment)
    - [How a Super Administrator assigns a template](#how-a-super-administrator-assigns-a-template)
    - [How someone else receives the Sandbox template](#how-someone-else-receives-the-sandbox-template)
    - [How a sandbox moves to another template](#how-a-sandbox-moves-to-another-template)
    - [Other caps that are not template fields](#other-caps-that-are-not-template-fields)
    - [Enforcement](#enforcement)
  - [Use Cases](#use-cases)
    - [1. SaaS Multi-Tier Pricing](#1-saas-multi-tier-pricing)
    - [2. Trial Periods](#2-trial-periods)
    - [3. Department or Business Unit Quotas](#3-department-or-business-unit-quotas)
    - [4. Partner and Integration Tiers](#4-partner-and-integration-tiers)
    - [5. Environment-Based Licenses](#5-environment-based-licenses)
  - [Security and Operational Best Practices](#security-and-operational-best-practices)
    - [Security](#security)
    - [Operational](#operational)
    - [Billing and Commercial](#billing-and-commercial)
    - [Compliance](#compliance)
  - [Troubleshooting](#troubleshooting)
    - [Workspace Cannot Create Clients](#workspace-cannot-create-clients)
    - [Request New Tenant Stays Disabled](#request-new-tenant-stays-disabled)
    - [Changing the Term Did Not Move the Expiration Date](#changing-the-term-did-not-move-the-expiration-date)
    - [License Quota Not Enforcing](#license-quota-not-enforcing)
    - [Cannot Delete a Template](#cannot-delete-a-template)
    - [Sandbox Was Archived](#sandbox-was-archived)
  - [Additional Resources](#additional-resources)
  - [Getting Help](#getting-help)

---

## Overview

License management controls how long a root workspace subscription lasts and what that workspace, and the child workspaces under it, may create.

Two records do this work:

- A **license template** is the tier. It holds the term and the quotas.
- A **license** is the subscription on one root workspace. It points at one template and stores the account holder and the expiration date.

Child workspaces do not hold their own license. They use the template on the root.

Every platform has two built-in templates:

| Template | Slug | Purpose |
| --- | --- | --- |
| Sandbox | `sandbox` | Short-lived workspace requested by someone who is not a Super Administrator |
| Standard | `standard` | Usual subscription for a root workspace |

Super Administrators can add custom templates. The slugs `sandbox` and `standard` are reserved.

A new installation seeds these values when the built-in rows are missing. Auth does not overwrite a Sandbox or Standard row that is already in the database. After an environment has been running, **Admin > License Templates** is the source of truth.

| | Sandbox | Standard |
| --- | --- | --- |
| Term | 14 days | 365 days |
| Child workspaces under the root | 2 | 100 |
| Clients per workspace | 3 | 100 |
| Users per workspace | 10 | 100 |
| Hourly login limit | Off | Off |

`0` is not unlimited. A child-workspace cap of `0` means the root may have no child workspaces. Client and user caps must be at least `1`. Leave hourly login limits off when a tier should not cap logins.

The configured platform tenant does not use a subscription license and is not capped by these limits. Super Administrators are not blocked by the client, user, or child-workspace caps when they create those resources. Everyone else is.

---

## Accessing License Management

**Location**: Admin > License Templates

**Prerequisites**:

- Super Administrator privileges
- Sign in to the Authifi UI and select any tenant from the tenant dropdown

**Navigation**:

- Open **Admin** in the left navigation menu
- Click **License Templates** to manage tiers
- Click **License** to view the subscription for the current workspace
- Click **Sandbox Requests** to review pending sandbox requests, when sandbox tenants are enabled

**Who sees each item**:

- **License Templates** is Super Administrator only
- **License** is available to Super Administrators and tenant administrators. Tenant administrators can view the license. They cannot change the tier
- **Sandbox Requests** appears only when sandbox tenants are enabled. It is enabled for sandbox tenant approvers. When the feature is off, the item is hidden

---

## License Management Operations

### Viewing License Templates

The License Templates dashboard lists every tier.

**Table columns**:

- **Name**: Display name of the tier
- **Slug**: Stable identifier. Built-in slugs are `sandbox` and `standard`
- **Term (days)**: Length copied onto a license when that license is created, upgraded, or renewed
- **Max clients / tenant**: Applications allowed in one workspace
- **Max users / tenant**: Memberships allowed in one workspace
- **Logins / hour**: Hard hourly limit, or "No limit" when login limits are off or the hard limit is unset
- **Built-in**: "Built-in" or "Custom"
- **Edit**: Open the template
- **Delete**: Remove a custom template. Hidden on Sandbox and Standard

Use the search box (**Search license templates**) to filter by the visible columns. The list is ordered by name.

Sandbox and Standard can be edited. They cannot be deleted, and their names and slugs cannot be changed.

---

### Creating a License Template

Create a tier with a term and resource quotas.

**Steps**:

- Click **ADD TEMPLATE**
- Fill in the creation dialog:
    - **Name**: Display name. At most 55 characters. Cannot be changed later
    - **Slug**: Unique identifier. At most 55 characters. Stored in lowercase. `sandbox` and `standard` are reserved. Cannot be changed later
    - **Term (days)**: Whole number, at least 1
    - **Max clients / tenant**: Whole number, at least 1
    - **Max users / tenant**: Whole number, at least 1
    - **Limit logins per hour**: Off means logins are not capped
- Click **Save**

The form starts from the Standard template's term, client cap, and user cap. The child-workspace cap is not on the form. A new custom template copies the Standard template's child-workspace cap. On a new installation that value is 100.

**Validation**:

- Name and slug are required and cannot be blank
- Slug must be unique after it is lowercased
- Term, max clients, and max users must be whole numbers of at least 1
- With login limits on, set a warning, a hard limit, or both. The warning must be lower than the hard limit

**Login limits**:

Turning the switch on for the first time offers a warning of 300 logins per hour and a hard limit of 400. The warning emails tenant administrators. The hard limit blocks new logins for the rest of that clock-hour. A later override on one license can lower that license's hard limit. It cannot raise it above the template.

**Example**:

```
Name: Professional
Slug: professional
Term (days): 365
Max clients / tenant: 50
Max users / tenant: 100
Limit logins per hour: Off
```

The child-workspace cap on this template matches Standard at the moment you save.

**Best practices**:

- Use descriptive, business-meaningful names
- Put the duration in the name when the term is the thing customers compare
- Document each published tier, including the child-workspace cap the form copied
- Set client and user caps above the immediate need so a small project does not hit the cap on day one

---

### Editing a License Template

Update the term, quotas, or login limits on an existing tier.

**Restrictions**:

- Built-in and custom templates can both be edited
- Name, slug, and the built-in flag cannot be changed

**Steps**:

- Click the **Edit** icon next to the template
- Modify the properties that are still enabled:
    - **Term (days)**
    - **Max clients / tenant**
    - **Max users / tenant**
    - **Limit logins per hour**, and the warning and hard limit when the switch is on
- Click **Save**

Changes behave as follows:

- **Increasing a cap**: Takes effect immediately. Workspaces on this template can create additional resources
- **Decreasing a cap**:
    - Does **not** delete clients, users, or child workspaces that are already over the new cap
    - Prevents creation of new resources beyond the new cap
    - Applies to every root workspace whose license uses this template
- **Changing the term**: Does not move the expiration date on licenses that already exist. The term is applied when a license is created, upgraded, or renewed

A workspace with 100 clients keeps them after Max clients / tenant is reduced to 50. It cannot create another client until the count is under 50. A Super Administrator can still create one.

**Best practices**:

- Audit current usage before reducing a cap
- Notify affected workspaces before the reduction
- Document the change. The new cap is live as soon as you save
- Change an existing subscription's expiration with Extend, Upgrade, or Renew on that license, not by editing the template term

---

### Deleting a License Template

Remove an unused custom tier.

**Restrictions**:

- Sandbox and Standard cannot be deleted. The delete icon is hidden
- A custom template cannot be deleted while any license still points at it
- Deletion is permanent

**Steps**:

- Click the **Delete** icon next to the template
- Confirm deletion in the dialog
- The template is permanently removed

**Pre-deletion checklist**:

- [ ] Confirm no license still uses this template
- [ ] Keep the template while any license still points at it
- [ ] Document the removal
- [ ] Tell stakeholders when the tier was published

A custom template can be deleted only when no license references it. **Upgrade** can move a Sandbox license onto another template. **Renew** keeps the current template, so a license that is no longer a sandbox stays on its template. There is no other way to reassign a license.

---

## Workspace Licenses

**Location**: Admin > License

This page shows the subscription for the workspace you currently have selected. Open it on the root workspace. A child workspace displays the root's license.

**Fields**:

- License name, which is the template name at the time the license was created or last upgraded
- Root tenant
- Template
- Account holder name, email, and phone
- Hourly login warning and hourly login limit, including an **Overridden** tag when this license differs from the template
- Created, last renewed, and expires

**Super Administrator actions**:

- **Edit account holder** updates the name, email, and phone stored on the license
- **Edit login limits** sets a per-license warning and hard limit. The hard limit can be stricter than the template. It cannot be looser. **Reset to tier** clears the override
- On a Sandbox license: **Extend** keeps the Sandbox template and moves the expiration. **Upgrade** moves the license onto a non-sandbox template
- On any other license: **Renew** starts a new term on the current template

A license is created with the root workspace. Its expiration is the template term, counted in UTC days from that moment. Only a root workspace can hold one. The configured platform tenant cannot.

---

## Sandbox Licenses

A sandbox license is a root workspace whose license uses the Sandbox template and whose tenant is marked as a sandbox. The shipped Sandbox template is 14 days, 2 child workspaces, 3 clients per workspace, and 10 users per workspace. Edit that row under **Admin > License Templates** when this environment should use different numbers. Existing rows are not rewritten from config.

Sandbox creation is separate from those quotas. Two platform settings control it. They are not fields on the template:

| Setting | Shipped default | Meaning |
| --- | --- | --- |
| `maxSandboxesPerUser` | 2 | How many active sandbox workspaces one user may have. Whole number, at least 1 |
| `sandBoxRequestRequireApproval` | Off | Off creates the sandbox immediately. On leaves the request pending until it is approved |

A Super Administrator changes these as platform sandbox settings. The License Templates screen does not edit them.

### Requesting a Sandbox

Someone who is not a Super Administrator uses **Request New Tenant** on the tenants page.

The request collects:

- Tenant name
- Description
- Account holder name (required, at most 255 characters)
- Account holder email (required, at most 255 characters)
- Account holder phone (at most 55 characters)
- Acceptance of the sandbox terms

**Request New Tenant** stays available while that user's active sandbox count is below `maxSandboxesPerUser`. Archiving, unarchiving, or deleting a workspace refreshes the count. At the allowance, the button is disabled.

With approval off, the workspace is created immediately and its license uses the Sandbox template. With approval on, the request waits under **Admin > Sandbox Requests**.

A Super Administrator does not use this form. They create the workspace directly and choose the template. That path is not limited by `maxSandboxesPerUser`.

### Approving a Sandbox Request

**Location**: Admin > Sandbox Requests

The dashboard lists pending requests first. Approved requests stay hidden unless you include them.

- A sandbox tenant approver can approve onto the Sandbox template only
- A Super Administrator can choose another template. Choosing Sandbox, or leaving the template unset, keeps the Sandbox license. Choosing any other template creates the workspace and upgrades that license in the same approval. The workspace is no longer a sandbox, and the new term starts from the target template
- If that upgrade fails after the workspace was created, the unapproved sandbox is removed and the request stays pending

The approval is recorded with the license, the template id, slug, and name, and the tenant id.

### Extending, Upgrading, and Renewing

From **Admin > License** on the sandbox root:

- **Extend** keeps the Sandbox template and sets a new expiration
- **Upgrade** is available only from a Sandbox license. Pick a non-sandbox template. The license name, template, and expiration change, the sandbox flag is cleared, and the new expiration is the target template's term counted from the upgrade

A license cannot be upgraded onto the Sandbox template. A non-sandbox license uses **Renew** to start a new term on its current template.

### Sandbox Expiration

The license-expiration job watches root licenses. The shipped job is limited to sandboxes (`sandboxOnly`) and warns 14 days before expiration.

- Inside the warning window, it emails the account holder, Super Administrators, and sandbox tenant approvers
- After the expiration date, it archives that sandbox root and the child workspaces under it, then sends an archived notice to the same people
- The configured platform tenant is never archived by this job
- Non-sandbox licenses are not archived by the shipped job. Renew them from **Admin > License** when the term should continue

Extend or upgrade a sandbox before the expiration date when the workspace should stay active.

---

## License Properties Reference

### Name

- **Type**: String
- **Required**: Yes
- **Unique**: The slug is the unique key. The name does not have to be unique
- **Max length**: 55 characters
- **Editable after create**: No
- **Examples**: "Sandbox", "Standard", "Professional", "Trial"

Use a customer-facing name for a published tier. The slug is the stable id.

### Slug

- **Type**: String
- **Required**: Yes
- **Unique**: Yes
- **Max length**: 55 characters
- **Format**: Trimmed and stored in lowercase
- **Reserved**: `sandbox`, `standard`
- **Editable after create**: No
- **Examples**: `professional`, `department-a`, `partner`

### Term (days)

- **Type**: Integer
- **Required**: Yes
- **Range**: 1 or greater
- **Applied**: When a license is created from this template, when a sandbox is upgraded onto it, or when a license is renewed
- **Not applied**: Editing the term does not change expiration dates already stored on licenses

The shipped Sandbox term is 14 days. The shipped Standard term is 365 days.

### Max clients / tenant

- **Type**: Integer
- **Required**: Yes
- **Range**: 1 or greater
- **Applied**: Per workspace, including each child workspace
- **Counts**: Clients in that workspace. A new import counts. Re-importing a client that already exists in the same workspace does not

Super Administrators are not blocked. Everyone else receives `You have reached the maximum number of clients (N) allowed for this workspace.`

### Max users / tenant

- **Type**: Integer
- **Required**: Yes
- **Range**: 1 or greater
- **Applied**: Per workspace
- **Counts**: Memberships in that workspace

Super Administrators are not blocked. Everyone else receives `You have reached the maximum number of users (N) allowed for this workspace.`

### Max child tenants

- **Type**: Integer
- **Required**: Yes on the template
- **Range**: 0 or greater. `0` means no child workspaces. It does not mean unlimited
- **On the create form**: Not shown. A custom template copies the Standard template's value
- **Counts**: Every descendant under the root. The root itself is not included. The cap is for the whole tree, not for each parent

Someone who is not a Super Administrator receives `You have reached the maximum number of child tenants (N) allowed under this subscription.`

### Hourly login warning and hourly login limit

- **Type**: Optional integers
- **Switch**: **Limit logins per hour**. Off means the tier does not cap logins
- **Range**: Each set value is a whole number of at least 1. The warning must be lower than the hard limit
- **Suggested start**: 300 warning, 400 hard limit
- **Warning**: Emails tenant administrators at that many successful interactive logins in the clock-hour
- **Hard limit**: Blocks new logins for the rest of that hour
- **Override**: A Super Administrator can lower the hard limit on one license from **Admin > License**. The override cannot raise it

### Account holder

Stored on the license, not on the template.

- **Name**: At most 255 characters
- **Email**: At most 255 characters
- **Phone**: At most 55 characters

A sandbox request collects these. A Super Administrator can edit them later from **Admin > License**. The expiration job emails the account holder address.

---

## License Assignment

A template is chosen when the root workspace is created, when a sandbox request is approved, or when a Super Administrator upgrades a sandbox license. The workspace keeps that license until a Super Administrator extends, upgrades, or renews it.

### How a Super Administrator assigns a template

- Open the tenants page
- Create the workspace directly
- Select the license template for that root
- Save

That workspace's license uses the selected template. Child workspaces created under it inherit the same template. This path is not limited by the per-user sandbox allowance.

### How someone else receives the Sandbox template

- They open the tenants page and choose **Request New Tenant**
- They submit the workspace and account holder details and accept the sandbox terms
- With approval off, the Sandbox license is created immediately
- With approval on, a sandbox tenant approver or a Super Administrator approves it under **Admin > Sandbox Requests**

### How a sandbox moves to another template

- Open the sandbox root
- Go to **Admin > License**
- Click **Upgrade**
- Select a non-sandbox template and save

The license now uses that template, the workspace is no longer a sandbox, and the expiration date is recalculated from the new term.

### Other caps that are not template fields

- **Sandbox allowance**: `maxSandboxesPerUser` limits how many active sandboxes one user may request. Shipped default is 2
- **Non-sandbox roots per user**: Someone who is not a Super Administrator and is creating a non-sandbox root is limited by `auth.defaults.defaultLicense.maxTenants`. The shipped default is 5. The message is `You have reached the maximum number of tenants (N) allowed.` This counts workspaces that user created. It is not the template's child-workspace cap

### Enforcement

Authifi enforces the template on the root license at the time of the action:

- **Client creation** is blocked at max clients per workspace
- **User membership** is blocked at max users per workspace
- **Child workspace creation** is blocked at max child tenants for the whole tree
- **Sandbox requests** are blocked at `maxSandboxesPerUser`

Lowering a cap does not delete existing resources. Super Administrators skip the client, user, child-workspace, and non-sandbox root caps.

---

## Use Cases

### 1. SaaS Multi-Tier Pricing

**Scenario**: Offer a short trial and a paid tier with different quotas.

**Configuration**:

```
Sandbox (built-in, edit the row if these numbers should change):
  Term: 14 days
  Child workspaces: 2
  Max clients / tenant: 3
  Max users / tenant: 10

Professional (custom template):
  Slug: professional
  Term: 365 days
  Max clients / tenant: 50
  Max users / tenant: 500
  Child workspaces: copied from Standard at create time
```

**Assignment**:

- New self-service workspaces arrive as sandboxes
- A Super Administrator upgrades a converted account onto `professional`

**Benefits**:

- The trial quota and the paid quota are different templates
- Enforcement follows the template on the root license
- The upgrade replaces the need to reassign a license from tenant metadata

### 2. Trial Periods

**Scenario**: Time-limited trials with a small client and user cap.

**Configuration**:

```
Sandbox:
  Term: 14 days
  Max clients / tenant: 3
  Max users / tenant: 10
```

**Implementation**:

- Leave `sandBoxRequestRequireApproval` off when the sandbox should exist as soon as the request is submitted
- Turn it on when a sandbox tenant approver must accept the request first
- The shipped expiration job warns 14 days before a sandbox expires, then archives the sandbox root and its child workspaces
- Upgrade the license before that date when the trial converts

The term on the template sets the expiration of a new sandbox. Editing the term later does not move sandboxes that already have an expiration date. Use **Extend** for those.

### 3. Department or Business Unit Quotas

**Scenario**: Internal roots with different client caps.

**Configuration**:

```
IT:
  Slug: it
  Max clients / tenant: 200
  Max users / tenant: 500

Marketing:
  Slug: marketing
  Max clients / tenant: 20
  Max users / tenant: 100
```

**Assignment**: A Super Administrator creates each department root with that template. Child workspaces under the root share it.

The child-workspace cap is whatever Standard had when the custom template was created. Plan that number before you click **ADD TEMPLATE**.

### 4. Partner and Integration Tiers

**Scenario**: A partner test workspace and a partner who manages child workspaces.

**Configuration**:

```
Integration partner:
  Max clients / tenant: 10
  Max users / tenant: 25
  Term: 365 days

Reseller:
  Max clients / tenant: 100
  Max users / tenant: 1000
  Child workspaces: the Standard value copied at create time
```

Create the partner root with the matching template. Child workspaces count toward the one tree-wide cap on that root.

### 5. Environment-Based Licenses

**Scenario**: Different caps for development, staging, and production roots.

**Configuration**:

```
Development:
  Max clients / tenant: 100
  Max users / tenant: 100

Staging:
  Max clients / tenant: 100
  Max users / tenant: 100

Production:
  Max clients / tenant: 200
  Max users / tenant: 2000
```

There is no unlimited client or user value. Set the development cap high enough for the work, and use a separate root so production quotas stay on the production template.

---

## Security and Operational Best Practices

### Security

- **Restrict template changes to Super Administrators**
    - License Templates is already limited to that role
    - Keep the Super Administrator group small
    - Tenant administrators can view **Admin > License**. They cannot edit the template

- **Monitor license changes**
    - Review audit logs for template edits, sandbox approvals, upgrades, and expiration archives
    - An approval record includes the license, the template id, slug, and name, and the tenant id
    - Alert on unexpected template deletions or quota drops

- **Validate who can raise a tier**
    - A sandbox tenant approver can approve only onto the Sandbox template
    - A Super Administrator is the role that can approve onto a paid template or upgrade a sandbox
    - Treat that upgrade as the commercial change. Authifi does not add a second approval workflow around it

- **Expect the Super Administrator bypass**
    - Client, user, and child-workspace caps do not stop a Super Administrator
    - That is the supported escape hatch. It is not a sign that enforcement is off for everyone else

### Operational

- **Read the live template before you quote a cap**
    - Shipped defaults apply when the built-in rows are first created
    - A later edit, or a config value used at first seed, is what this environment enforces

- **Plan a cap reduction**
    - Count clients, users, and child workspaces first
    - Existing resources stay
    - New creates fail until usage is back under the cap

- **Separate the sandbox allowance from the Sandbox template**
    - `maxSandboxesPerUser` answers "why is Request New Tenant disabled?"
    - Max clients / tenant answers "why can this sandbox not create another application?"

- **Extend or upgrade before the archive**
    - The shipped job archives an expired sandbox and its child workspaces
    - Renew non-sandbox licenses from **Admin > License**. The shipped job does not archive them

- **Test the limit you changed**
    - Sign in as someone who is not a Super Administrator
    - Confirm the next create fails at the new cap and that a Super Administrator can still create

### Billing and Commercial

- **Align the template with the published tier**
    - Name, term, client cap, and user cap are what the product enforces
    - Record the child-workspace cap in the same place you publish the tier, because the form does not show it

- **Use Upgrade for a conversion**
    - Upgrade recalculates the expiration from the target term and clears the sandbox flag
    - Extend keeps a trial on the Sandbox template

- **Review assignments**
    - **Admin > License** on the root shows the template that workspace is actually on
    - Child workspaces show that same root license

### Compliance

- **Keep the account holder current**
    - Expiration notices go to that email, plus Super Administrators and, for sandboxes, sandbox approvers

- **Do not delete a tier that a contract still references**
    - Built-in templates cannot be deleted
    - A custom template cannot be deleted while a license points at it

- **Record exceptions**
    - A per-license login override is visible on **Admin > License** as **Overridden**
    - Reset it to the tier when the exception ends

---

## Troubleshooting

### Workspace Cannot Create Clients

**Symptom**: `You have reached the maximum number of clients (N) allowed for this workspace.`

**Solution**:

- Open **Admin > License** on that workspace and note the template
- Compare the workspace's client count with **Max clients / tenant** on that template
- Options:
    - Upgrade a sandbox onto a template with a higher cap
    - Increase **Max clients / tenant** on the current template. This affects every workspace on that template
    - Delete unused clients in the workspace

A Super Administrator can still create a client. The same pattern applies to users, with `You have reached the maximum number of users (N) allowed for this workspace.`

### Request New Tenant Stays Disabled

**Symptom**: A user cannot start another sandbox.

**Checks**:

- Compare that user's active sandbox count with `maxSandboxesPerUser`
- This allowance is not the Sandbox template's client or user cap
- Archiving or deleting one of that user's sandbox workspaces frees a slot

A Super Administrator creates a workspace from the tenants page and is not held to this allowance.

### Changing the Term Did Not Move the Expiration Date

**Symptom**: **Admin > License** still shows the old expiration after the template term was edited.

**Reason**: The term is copied onto the license when the license is created, upgraded, or renewed.

**Solution**: Use **Extend** or **Upgrade** on a sandbox, or **Renew** on any other license.

### License Quota Not Enforcing

**Symptom**: More resources can be created than the template allows.

**Checks**:

- Confirm the caller is not a Super Administrator. That role skips the client, user, and child-workspace caps
- Confirm the workspace is not the configured platform tenant. That tenant is not licensed
- Read the template on the root license. A child workspace does not have a separate cap
- Confirm the cap you edited is the one being counted. Child workspaces use max child tenants. Clients use max clients per workspace

### Cannot Delete a Template

**Symptom**: The delete icon is missing, or the delete fails.

**Reasons**:

- Sandbox and Standard cannot be deleted
- A custom template is still referenced by a license
- The signed-in user is not a Super Administrator

**Solution**: Leave Sandbox and Standard in place. Keep a custom template while any license still references it. **Upgrade** can move a Sandbox license onto another template first. **Renew** does not change the template.

### Sandbox Was Archived

**Symptom**: A sandbox root and its child workspaces are archived after the expiration date.

**Reason**: The license-expiration job archives expired sandboxes. The shipped job warns 14 days before that date.

**Solution**: Extend or upgrade the sandbox before expiration. Restoring an archived workspace is a separate tenant operation. It does not by itself give the license a new term.

---

## Additional Resources

- [Tenant Administrator Guide](tenant-admin-guide.md)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)

---

## Getting Help

For assistance with license management:

- **Open Admin > License Templates** and read the caps this environment is actually enforcing
- **Open Admin > License** on the root workspace and confirm the template, account holder, and expiration
- **Check Sandbox Requests** when a request is still pending
- **Review audit logs** for template edits, approvals, and upgrades
- **Contact a Super Administrator** before lowering a cap that other workspaces share

---

**Document version**: 2.0  
**Last updated**: 2026-10-26  
**Target audience**: Super Administrators  
**Scope**: License templates, workspace licenses, and sandbox licenses

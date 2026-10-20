# License Management Guide (Super Admins)

Super Administrators manage platform licenses through the Authifi UI.

## Table of Contents

- [Overview](#overview)
- [Accessing License Management](#accessing-license-management)
- [License Management Operations](#license-management-operations)
  - [Viewing Licenses](#viewing-licenses)
  - [Creating a License](#creating-a-license)
  - [Editing a License](#editing-a-license)
  - [Deleting a License](#deleting-a-license)
- [License Properties Reference](#license-properties-reference)
- [License Assignment](#license-assignment)
- [Use Cases](#use-cases)
- [Security and Operational Best Practices](#security-and-operational-best-practices)

---

## Overview

License management is a **Super Administrator-only** feature that controls tenant and application quotas across Authifi. Licenses support:

- Multi-tier SaaS offerings (Free, Pro, Enterprise)
- Department or business unit quotas
- Trial period limitations
- Resource allocation and billing control

The **Admins > Licenses** menu is hidden for non-Super-Administrator users. Only Super Administrators can view or manage licenses.

---

## Accessing License Management

**Location**: Admins > Licenses

**Prerequisites**:

- Super Administrator privileges
- The Admins menu will not appear for tenant admins or regular users

**Navigation**:

- Log in to the Authifi UI with super admin credentials
- Select any tenant from the tenant dropdown
- Navigate to **Admins** in the left navigation menu
- Click **Licenses**

---

## License Management Operations

### Viewing Licenses

The Licenses dashboard lists all configured license tiers.

**Table columns**:

- **Name**: Unique license identifier
- **Max Tenants**: Maximum number of tenants allowed (0 = unlimited)
- **Max Clients**: Maximum number of applications per tenant (0 = unlimited)
- **Settings**: Edit license (hidden for default license)
- **Delete**: Remove license (hidden for default license)

Use the search box to filter licenses by name.

Every platform has a "Default License" that:

- Cannot be edited
- Cannot be deleted
- Is the fallback when no other license is assigned
- Typically configured with platform-wide default limits

---

### Creating a License

Create a license tier with resource quotas.

**Steps**:

- Click **+ ADD LICENSE** button
- Fill in the license creation dialog:
    - **Name\***: Unique identifier for the license tier
    - **Max Tenants**: Tenant quota (0 = unlimited)
    - **Max Clients**: Application quota per tenant (0 = unlimited)
- Click **Save**

**Validation**:

- Name is required and must be unique
- Max Tenants and Max Clients must be non-negative integers

**Example**:

```
Name: Professional
Max Tenants: 0 (unlimited tenants can use this license)
Max Clients: 50 (each tenant can have up to 50 applications)
```

**Best practices**:

- Use descriptive, business-meaningful names
- Document license tiers in your product documentation
- Consider growth patterns when setting limits
- Leave room for expansion (set limits higher than immediate needs)

---

### Editing a License

Update quota limits for an existing license tier.

**Restrictions**:

- Cannot edit the "Default License"
- Can only edit custom licenses

**Steps**:

- Click the **Settings** icon (gear) next to the license
- Modify license properties:
    - **Max Tenants**: Update tenant quota
    - **Max Clients**: Update application quota
- Click **Save**

Changes behave as follows:

- **Increasing limits**: Takes effect immediately; tenants can create additional resources
- **Decreasing limits**:
  - Does **not** automatically delete existing resources that exceed new limits
  - Prevents creation of new resources beyond the new limit
  - Consider communicating with affected tenants before reducing limits

Reducing limits does not trigger automatic cleanup. A tenant with 100 clients keeps them after Max Clients is reduced to 50, but cannot create more until its count is below the limit.

**Best practices**:

- Audit current usage before reducing limits
- Notify affected tenants before limit reductions
- Provide grace period for compliance
- Document limit changes in changelog

---

### Deleting a License

Remove an unused license tier.

**Restrictions**:

- Cannot delete the "Default License"
- Can only delete custom licenses
- Should not delete licenses currently assigned to tenants (verify first)

**Steps**:

- Click the **Delete** icon (trash) next to the license
- Confirm deletion in the dialog
- The license is permanently removed.

**Pre-deletion checklist**:

- [ ] Verify no tenants are currently assigned this license
- [ ] Re-assign affected tenants to different licenses (if any)
- [ ] Document the removal in your change log
- [ ] Communicate to stakeholders if this was a published tier

**What happens to assigned tenants?**

- If tenants are assigned a deleted license, they may fall back to the default license
- **Recommendation**: Re-assign tenants to appropriate licenses before deleting

**Best practice**: Archive documentation about deleted licenses for historical reference and billing records.

---

## License Properties Reference

### Name

- **Type**: String
- **Required**: Yes
- **Unique**: Yes
- **Max length**: Varies (typically 50-100 characters)
- **Format**: Alphanumeric with spaces, hyphens, underscores allowed
- **Examples**: "Free Tier", "Professional", "Enterprise", "Trial-30-Day", "Department-A"

**Naming conventions**:

- Use title case for customer-facing tiers (e.g., "Professional", "Enterprise")
- Use descriptive names for internal tiers (e.g., "Internal-Development", "Partner-Integration")
- Include duration for time-limited licenses (e.g., "Trial-14-Day")

### Max Tenants

- **Type**: Integer
- **Required**: Yes
- **Range**: 0 to unlimited
- **Special value**: `0` means unlimited tenants

**Interpretation**:

- Maximum number of tenants that can be created with this license
- Used for platform-wide licensing (not tenant-specific quotas)
- **Use case**: Control how many tenant instances a customer organization can create

**Examples**:

- **Free tier**: `1` (single tenant per account)
- **Professional**: `5` (multi-tenant for small teams)
- **Enterprise**: `0` (unlimited tenants)

**Note**: This typically limits tenant creation at the platform/customer level, not per-tenant quotas.

### Max Clients

- **Type**: Integer
- **Required**: Yes
- **Range**: 0 to unlimited
- **Special value**: `0` means unlimited clients

**Interpretation**:

- Maximum number of OAuth 2.0/OIDC clients or SAML service providers a tenant can create
- Applied per tenant (each tenant under this license gets this quota)
- **Use case**: Control application integration density

**Examples**:

- **Free tier**: `5` (up to 5 apps)
- **Professional**: `50` (moderate integration needs)
- **Enterprise**: `0` (unlimited apps)

**Planning guidance**:

- Consider typical customer integration patterns
- Factor in dev/staging/production environments (3x multiplier)
- Allow headroom for growth (don't set exact limits)
- Monitor actual usage to inform tier definitions

---

## License Assignment

Licenses are assigned to tenants via **Tenant Settings**.

### How to Assign a License to a Tenant

- Navigate to **Tenant > Settings**
- Click **Edit**
- Go to the **Metadata** tab
- In **License Assignment** dropdown, select the license
- Click **Save**

License assignment is available in the tenant edit dialog's Metadata section. Only Super Administrators can change tenant license assignments.

### Viewing Tenant's Current License

- Open Tenant Details or Tenant Edit dialog
- Check the Metadata tab for current license assignment
- The license name and limits are displayed

### Enforcement

Authifi enforces license limits:

- **Client creation**: Blocked when Max Clients limit is reached
- **Tenant creation**: Blocked when Max Tenants limit is reached (at platform level)
- **API responses**: Return error with quota exceeded message

Applications should handle quota errors and tell users when they need a license upgrade.

---

## Use Cases

### 1. SaaS Multi-Tier Pricing

**Scenario**: Offer Free, Professional, and Enterprise tiers with different capabilities.

**Configuration**:

```
Free Tier:
  Max Tenants: 1
  Max Clients: 5

Professional:
  Max Tenants: 5
  Max Clients: 50

Enterprise:
  Max Tenants: 0 (unlimited)
  Max Clients: 0 (unlimited)
```

**Benefits**:

- Clear differentiation between tiers
- Automated enforcement of limits
- Easy upgrades (just change license assignment)

### 2. Trial Periods

**Scenario**: Offer time-limited trials with reduced quotas.

**Configuration**:

```
Trial-14-Day:
  Max Tenants: 1
  Max Clients: 3
```

**Implementation**:

- Assign "Trial-14-Day" license to new accounts
- Set user expiration or automate license change after 14 days
- Monitor trial usage in analytics

Combine license limits with tenant-level expiration dates to control trial quotas and duration.

### 3. Department or Business Unit Quotas

**Scenario**: Internal deployment with different departments having different limits.

**Configuration**:

```
IT-Department:
  Max Tenants: 0
  Max Clients: 200 (large integration needs)

Marketing-Department:
  Max Tenants: 0
  Max Clients: 20 (fewer integrations)

Finance-Department:
  Max Tenants: 0
  Max Clients: 50
```

**Benefits**:

- Cost allocation per department
- Prevent resource monopolization
- Enforce governance policies

### 4. Partner and Integration Tiers

**Scenario**: Different limits for different types of partners.

**Configuration**:

```
Integration-Partner:
  Max Tenants: 1
  Max Clients: 10 (for partner's integration testing)

Reseller-Partner:
  Max Tenants: 0 (unlimited - they manage sub-tenants)
  Max Clients: 100 (per sub-tenant)
```

**Use case**: Partner ecosystem with varied needs.

### 5. Environment-Based Licenses

**Scenario**: Different limits for dev/staging/production.

**Configuration**:

```
Development:
  Max Tenants: 0
  Max Clients: 0 (no limits in dev)

Staging:
  Max Tenants: 0
  Max Clients: 100

Production:
  Max Tenants: 1
  Max Clients: 200
```

**Benefits**:

- Generous limits in development
- Production parity in staging
- Controlled production deployment

---

## Security and Operational Best Practices

### Security

- **Restrict access to super admins only**
    - License management affects billing and resource allocation
    - Limit super admin role to trusted personnel
    - Audit super admin actions regularly

- **Monitor license changes**
    - Review audit logs for license modifications
    - Alert on unexpected license deletions or quota changes
    - Track who made changes and when

- **Validate license assignments**
    - Ensure tenants have appropriate licenses
    - Prevent unauthorized license tier escalations
    - Implement approval workflows for license upgrades (external to Authifi system)

- **Protect quota integrity**
    - Don't set limits that could be easily bypassed
    - Monitor for quota circumvention attempts
    - Investigate accounts repeatedly hitting limits

### Operational

- **Plan capacity**
    - Set limits based on infrastructure capacity
    - Monitor aggregate resource usage across all tenants
    - Plan scaling before reaching platform limits

- **Document license tiers**
    - Maintain clear documentation of what each tier includes
    - Publish tier limits in customer-facing documentation
    - Keep internal runbooks for tier management

- **Monitor quota usage**
    - Track tenants approaching their limits
    - Proactive outreach for upgrade opportunities
    - Alert operations team when limits are reached

- **Version control license definitions**
    - Document when licenses were created/modified
    - Track historical limit changes
    - Maintain changelog for compliance

- **Test limit enforcement**
    - Verify quota limits are properly enforced
    - Test error handling when limits are exceeded
    - Validate that unlimited (0) works correctly

### Billing and Commercial

- **Align with pricing model**
    - Ensure license limits match published pricing tiers
    - Keep licenses synchronized with billing system
    - Update licenses when pricing changes

- **Handle upgrades/downgrades**
    - Document upgrade process (increase limits)
    - Define downgrade policy (reduce limits)
    - Communicate changes to affected tenants

- **Audit license assignments**
    - Regularly review tenant-to-license mappings
    - Identify tenants on incorrect licenses
    - Ensure billing matches assigned licenses

- **Grace periods for limit breaches**
    - Consider allowing temporary overages
    - Notify tenants approaching limits
    - Provide upgrade path before hard enforcement

### Compliance

- **Record retention**
    - Maintain records of license assignments
    - Track license changes for auditing
    - Correlate with billing records

- **Access audit**
    - Log all license management operations
    - Review who accessed license management UI
    - Investigate unauthorized access attempts

- **Contractual obligations**
    - Ensure licenses enforce contractual limits
    - Validate limits match customer agreements
    - Document exceptions with approval

---

## Troubleshooting

### Tenant Cannot Create Clients

**Symptom**: Error message "Maximum clients limit reached" when creating application.

**Solution**:

- Check tenant's assigned license (Tenant Settings > Metadata)
- View current client count vs. Max Clients limit
- Options:
    - Upgrade tenant to higher license tier
    - Increase Max Clients for current license
    - Have tenant delete unused clients

### License Quota Not Enforcing

**Symptom**: Tenant can create more resources than license allows.

**Checks**:

- Verify license is correctly assigned to tenant
- Check if Max Clients is set to `0` (unlimited)
- Review audit logs for recent license changes
- Confirm no system-admin bypass in place

**Resolution**: Contact development team if enforcement is broken.

### Cannot Delete License

**Symptom**: Delete button not visible or operation fails.

**Reasons**:

- Attempting to delete "Default License" (not allowed)
- License is currently assigned to tenants (best practice: re-assign first)
- Not logged in as super admin

**Solution**: Re-assign affected tenants to different licenses before deleting.

---

## Additional Resources

- [Tenant Administrator Guide](tenant-admin-guide.md) (for license assignment process)
- [Super Admin Access Reference](../authorization/super-admin-access.md)
- [Admin Roles Overview](../authorization/admin-roles.md)

---

## Getting Help

For assistance with license management:

- **Review audit logs** for recent license changes
- **Check tenant assignments** to verify correct licenses
- **Monitor quota usage** in tenant dashboards
- **Consult platform documentation** for commercial/billing policies
- **Contact platform administrators** for policy questions

---

**Document version**: 1.0  
**Last updated**: 2025-12-12  
**Target audience**: Super Administrators  
**Scope**: Platform-wide license quota management

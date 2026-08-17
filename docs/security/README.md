# Authifi Service - Security Documentation

## Overview

This directory contains guidance for configuring and operating Authifi administrative accounts in accordance with FedRAMP Recommended Secure Configuration requirements.

---

## Security Admin Guide

**Document**: [Security Admin Guide](./security-admin-guide.md)

The Security Admin Guide covers administrative account setup, configuration, operation, and decommissioning.

### Administrative Account Types Covered

| Account Type              | Description                                                  |
| ------------------------- | ------------------------------------------------------------ |
| **Super Administrators**  | Top-level administrative accounts with platform-wide control |
| **Tenant Administrators** | Privileged accounts with full control within a single tenant |
| **Delegated Admins**      | Users with specific elevated permissions for particular resources |

### Key Topics

- Administrative account role definitions (permissions, actions, operations)
- Account lifecycle procedures (setup, MFA requirements, configuration, decommissioning)
- Security settings reference tables with recommended values
- FedRAMP compliance mapping

---

## Recommended Secure Configuration

**Document**: [Recommended Secure Configuration](./recommended-secure-configuration.md)

The Recommended Secure Configuration guide covers:

- Authentication and session management settings
- Identity provider security configuration
- Role-based access control (RBAC) settings
- Data protection and encryption
- Monitoring and audit configuration
- Recommended security practices
- Compliance checklists

---

## Quick Reference

### For Super Administrators

- [Super Administrator Role Definition](./security-admin-guide.md#super-administrators-top-level-administrative-accounts)
- [Super Administrator Lifecycle](./security-admin-guide.md#super-administrator-lifecycle)
- [Super Administrator Security Settings](./security-admin-guide.md#super-administrator-security-settings-reference)

### For Tenant Administrators

- [Tenant Administrator Role Definition](./security-admin-guide.md#tenant-administrators-privileged-accounts)
- [Tenant Administrator Lifecycle](./security-admin-guide.md#tenant-administrator-lifecycle)
- [Tenant Administrator Security Settings](./security-admin-guide.md#tenant-administrator-security-settings-reference)

### For Delegated Admins

- [Delegated Admin Role Definition](./security-admin-guide.md#delegated-admins)
- [Admin Scope Permissions](./security-admin-guide.md#admin-scope-capabilities)
- [UMRS Delegated Administration](./security-admin-guide.md#user-managed-role-system-umrs)

---

## Related Documentation

- [Tenant Administrator Guide](../guides/tenant-admin-guide.md) - UI-focused tenant settings guide
- [Super Admin Access Requirements](../authorization/super-admin-access.md) - Complete list of super-admin-only operations
- [Admin Roles Overview](../authorization/admin-roles.md) - Technical role implementation details

---

**Classification**: Public  
**Last Updated**: 2026-01-22

"""Typed authorization identifier catalogs.

These StrEnum catalogs are the single source of truth for the slugs used at
call sites (dependency factories, policies, services). The database
``permissions`` and ``roles`` tables are the runtime mirror, seeded from
frozen snapshots of these values inside Alembic data migrations; committed
migrations never import this module.

Using enum members at call sites makes a typo an AttributeError at import
time instead of a nonexistent permission string that silently denies
everyone.
"""

from enum import StrEnum

# Slug of the protected platform tenant whose users alone are eligible for
# system-role assignments. The tenant row itself is created by a later
# bootstrap migration.
PLATFORM_TENANT_SLUG = "platform"


class PermissionSlug(StrEnum):
    """Global, code-defined catalog of permission slugs.

    TODO: Extend as new resources (reports, integrations, billing) come
    into scope; every addition ships with a new additive seed migration.
    """

    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    ROLES_READ = "roles:read"
    ROLES_MANAGE = "roles:manage"
    CAMPAIGNS_READ = "campaigns:read"
    CAMPAIGNS_MANAGE = "campaigns:manage"


PERMISSION_DESCRIPTIONS: dict[PermissionSlug, str] = {
    PermissionSlug.USERS_READ: "View users belonging to the tenant.",
    PermissionSlug.USERS_MANAGE: "Invite, update, and deactivate tenant users.",
    PermissionSlug.ROLES_READ: "View roles and their permissions.",
    PermissionSlug.ROLES_MANAGE: "Create, update, assign, and revoke roles.",
    PermissionSlug.CAMPAIGNS_READ: "View advertising campaigns.",
    PermissionSlug.CAMPAIGNS_MANAGE: "Create, update, and delete advertising campaigns.",
}


class BuiltInRoleSlug(StrEnum):
    """Slugs of the global built-in tenant roles (tenant_id IS NULL)."""

    TENANT_ADMIN = "tenant_admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"
    VIEWER = "viewer"


class SystemRoleSlug(StrEnum):
    """Slugs of platform-wide system roles (system_role_assignments)."""

    SUPER_ADMIN = "super_admin"

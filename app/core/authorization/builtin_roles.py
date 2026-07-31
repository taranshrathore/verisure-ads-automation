"""Built-in role definitions: display names and permission sets.

This module is the application-side source of truth used to *generate* the
frozen seed migrations and to drive future catalog-vs-database consistency
tests. Committed Alembic migrations contain their own literal snapshots and
never import this module; an applied migration is not a runtime
synchronizer.
"""

from app.core.authorization.catalog import BuiltInRoleSlug, PermissionSlug

BUILTIN_ROLE_NAMES: dict[BuiltInRoleSlug, str] = {
    BuiltInRoleSlug.TENANT_ADMIN: "Tenant Administrator",
    BuiltInRoleSlug.MANAGER: "Manager",
    BuiltInRoleSlug.EMPLOYEE: "Employee",
    BuiltInRoleSlug.VIEWER: "Viewer",
}

BUILTIN_ROLE_PERMISSIONS: dict[BuiltInRoleSlug, frozenset[PermissionSlug]] = {
    BuiltInRoleSlug.TENANT_ADMIN: frozenset(
        {
            PermissionSlug.USERS_READ,
            PermissionSlug.USERS_MANAGE,
            PermissionSlug.ROLES_READ,
            PermissionSlug.ROLES_MANAGE,
            PermissionSlug.CAMPAIGNS_READ,
            PermissionSlug.CAMPAIGNS_MANAGE,
        }
    ),
    BuiltInRoleSlug.MANAGER: frozenset(
        {
            PermissionSlug.USERS_READ,
            PermissionSlug.ROLES_READ,
            PermissionSlug.CAMPAIGNS_READ,
            PermissionSlug.CAMPAIGNS_MANAGE,
        }
    ),
    BuiltInRoleSlug.EMPLOYEE: frozenset(
        {
            PermissionSlug.CAMPAIGNS_READ,
            PermissionSlug.CAMPAIGNS_MANAGE,
        }
    ),
    BuiltInRoleSlug.VIEWER: frozenset(
        {
            PermissionSlug.CAMPAIGNS_READ,
        }
    ),
}

"""Immutable, framework-agnostic authorization context.

The context is an eager snapshot of a user's effective authorization state,
built once per request. It holds only primitives and frozensets: no ORM
objects, no session, nothing framework-specific.
"""

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.catalog import PermissionSlug, SystemRoleSlug


@dataclass(frozen=True)
class AuthorizationContext:
    """Snapshot of a user's effective authorization state for one request.

    ``permissions`` holds typed PermissionSlug members, normalized exactly
    once by AuthorizationService.build_context (unknown database slugs are
    logged and discarded there). ``tenant_roles`` and ``system_roles`` hold
    raw slug strings as returned by the database. ``has_permission`` is the
    single canonical location of the super_admin bypass; everything else
    (has_all, has_any, AuthorizationService.require, policies) delegates
    to it.

    The ``permissions`` field remains directly readable as data for
    consumers that must reason about what the user literally holds (e.g.
    subset-delegation checks in the future RoleManagementService) rather
    than what they can effectively do.
    """

    user_id: UUID
    tenant_id: UUID
    permissions: frozenset[PermissionSlug]
    tenant_roles: frozenset[str]
    system_roles: frozenset[str]

    @property
    def is_super_admin(self) -> bool:
        """Return True when the user holds the super_admin system role."""
        return SystemRoleSlug.SUPER_ADMIN in self.system_roles

    def has_permission(self, permission: PermissionSlug) -> bool:
        """Return effective authorization; sole location of the super_admin bypass."""
        if self.is_super_admin:
            return True
        return permission in self.permissions

    def has_all(self, *permissions: PermissionSlug) -> bool:
        """Return True when every given permission is effectively held."""
        return all(self.has_permission(permission) for permission in permissions)

    def has_any(self, *permissions: PermissionSlug) -> bool:
        """Return True when at least one given permission is effectively held."""
        return any(self.has_permission(permission) for permission in permissions)

    def has_system_role(self, system_role: SystemRoleSlug) -> bool:
        """Return exact system-role membership; never bypassed by other system roles."""
        return system_role in self.system_roles

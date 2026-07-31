"""Read-only authorization engine: context building and effective checks.

Strictly read-only: never assigns, revokes, commits, or rolls back. Grants
and revocations belong to the future RoleManagementService (tenant roles)
and SystemRoleManagementService (system roles, which owns the
platform-tenant eligibility invariant on the write side).
"""

from uuid import UUID

from app.core.authorization.catalog import PermissionSlug, SystemRoleSlug
from app.core.authorization.context import AuthorizationContext
from app.core.exceptions import PermissionDeniedError
from app.core.logging import logger
from app.repositories.authorization_repository import AuthorizationRepository

_KNOWN_PERMISSION_SLUGS = frozenset(slug.value for slug in PermissionSlug)


class AuthorizationService:
    """Builds AuthorizationContext snapshots and performs effective checks."""

    def __init__(self, authorization_repository: AuthorizationRepository) -> None:
        self._authorization_repository = authorization_repository

    def build_context(
        self,
        user_id: UUID,
        tenant_id: UUID,
        is_platform_tenant: bool,
    ) -> AuthorizationContext:
        """Resolve and freeze the user's authorization state for one request.

        System roles are honored only for platform-tenant users. If the
        database contains system-role rows for a customer-tenant user
        (corrupted data), the context is built fail-closed with no system
        roles and a high-severity security alert is logged; the read path
        never mutates data.
        """
        role_slugs = self._authorization_repository.get_active_tenant_role_slugs(
            user_id, tenant_id
        )
        permission_slugs = (
            self._authorization_repository.get_effective_permission_slugs(
                user_id, tenant_id
            )
        )
        system_role_slugs = (
            self._authorization_repository.get_active_system_role_slugs(user_id)
        )

        if system_role_slugs and not is_platform_tenant:
            logger.critical(
                "SECURITY ALERT: user %s of customer tenant %s holds system "
                "role(s) %s; ignoring them (fail closed). Investigate and "
                "revoke the offending system_role_assignments rows.",
                user_id,
                tenant_id,
                sorted(system_role_slugs),
            )
            system_role_slugs = []

        # Normalize database slugs into typed PermissionSlug members exactly
        # once; unknown slugs are logged and discarded, never stored.
        unknown_slugs = set(permission_slugs) - _KNOWN_PERMISSION_SLUGS
        if unknown_slugs:
            logger.warning(
                "User %s holds permission slug(s) unknown to this code "
                "version: %s. They are discarded from the authorization "
                "context.",
                user_id,
                sorted(unknown_slugs),
            )
        permissions = frozenset(
            PermissionSlug(slug)
            for slug in permission_slugs
            if slug in _KNOWN_PERMISSION_SLUGS
        )

        return AuthorizationContext(
            user_id=user_id,
            tenant_id=tenant_id,
            permissions=permissions,
            tenant_roles=frozenset(role_slugs),
            system_roles=frozenset(system_role_slugs),
        )

    def require(
        self, context: AuthorizationContext, permission: PermissionSlug
    ) -> None:
        """Raise PermissionDeniedError unless the permission is effectively held.

        Delegates entirely to context.has_permission, which owns the
        super_admin bypass; this method adds only the raise.
        """
        if not context.has_permission(permission):
            raise PermissionDeniedError()

    def require_system_role(
        self, context: AuthorizationContext, system_role: SystemRoleSlug
    ) -> None:
        """Raise PermissionDeniedError unless the exact system role is held.

        Never bypassed: holding one system role does not imply another.
        """
        if not context.has_system_role(system_role):
            raise PermissionDeniedError()

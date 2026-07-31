"""Tenant-scoped role management: create/update/delete custom roles and
assign/revoke role grants.

RoleManagementService owns all transaction commits; repositories never
commit. Every operation is scoped to exactly the tenant_id passed by the
caller (taken from the caller's own AuthorizationContext.tenant_id at the
API layer) -- never a tenant_id sourced from a request body/path without
that cross-check happening upstream.

Last-tenant-administrator locking invariant (binding on all future code,
not just this milestone): any operation capable of reducing a tenant's
count of qualifying active administrators (a non-deleted user holding an
active builtin tenant_admin assignment) MUST, within one transaction:
    1. lock the tenant row first (TenantRepository.lock_for_update);
    2. evaluate the post-operation admin count
       (UserRoleAssignmentRepository.count_active_tenant_admins);
    3. perform the mutation.
This milestone implements this only for revoke_assignment. Future
user-deactivation/soft-deletion or bulk-revocation code must follow the
same lock ordering (tenant row before assignment rows) rather than
inventing a second, incompatible locking strategy.

Subset delegation: any operation that would cause a role to grant, or a
user to hold via assignment, a permission set beyond what the acting
caller already effectively holds (per AuthorizationContext.has_all,
which already encodes the super_admin bypass) is rejected with
PermissionDeniedError. This applies to create_custom_role, update_role,
and assign_role. It is not duplicated logic: it delegates entirely to
AuthorizationContext.has_all.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization.catalog import BuiltInRoleSlug, PermissionSlug
from app.core.authorization.context import AuthorizationContext
from app.core.exceptions import (
    LastTenantAdminError,
    PermissionDeniedError,
    PermissionNotFoundError,
    ProtectedRoleError,
    RoleAssignmentConflictError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.models.role import Role
from app.models.user_role_assignment import UserRoleAssignment
from app.repositories.role_repository import RoleRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.repositories.user_role_assignment_repository import (
    UserRoleAssignmentRepository,
)

_KNOWN_PERMISSION_SLUGS = frozenset(slug.value for slug in PermissionSlug)
_RESERVED_BUILTIN_SLUGS = frozenset(slug.value for slug in BuiltInRoleSlug)

# Constraint names this service knows how to translate into a friendly
# domain exception. Any other IntegrityError (e.g. an unrelated NOT NULL or
# foreign-key violation) must never be mislabeled as one of these -- it is
# rolled back and re-raised as-is so it surfaces as an unexpected error
# rather than a misleading 409.
_ROLE_SLUG_CONFLICT_CONSTRAINTS = frozenset(
    {"uq_roles_tenant_id_slug_custom", "uq_roles_slug_builtin"}
)
_DUPLICATE_ASSIGNMENT_CONSTRAINT = "uq_user_role_assignments_user_id_role_id_active"


def _constraint_name(exc: IntegrityError) -> str | None:
    """Best-effort extraction of the violated constraint's name.

    Relies on psycopg's diagnostics (exc.orig.diag.constraint_name); returns
    None if unavailable, which safely falls through to re-raising the
    original error rather than guessing.
    """
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


class RoleManagementService:
    """Orchestrates tenant-scoped role and role-assignment use cases."""

    def __init__(
        self,
        role_repository: RoleRepository,
        user_role_assignment_repository: UserRoleAssignmentRepository,
        user_repository: UserRepository,
        tenant_repository: TenantRepository,
        session: Session,
    ) -> None:
        self._roles = role_repository
        self._assignments = user_role_assignment_repository
        self._users = user_repository
        self._tenants = tenant_repository
        self._session = session

    def list_roles(self, tenant_id: UUID) -> list[Role]:
        """Return every role visible to a tenant (builtins + its active custom roles)."""
        return self._roles.list_for_tenant(tenant_id)

    def get_permission_slugs(self, role_id: UUID) -> list[str]:
        """Return the permission slugs attached to a role."""
        return self._roles.get_permission_slugs_for_role(role_id)

    def create_custom_role(
        self,
        tenant_id: UUID,
        slug: str,
        name: str,
        permission_slugs: Sequence[str],
        actor_context: AuthorizationContext,
    ) -> Role:
        """Create a tenant-owned custom role with the given permission set."""
        validated_permissions = self._validate_permission_slugs(permission_slugs)
        self._require_subset_delegation(actor_context, validated_permissions)
        self._require_available_slug(tenant_id, slug)

        role_id = uuid.uuid4()
        role = Role(id=role_id, tenant_id=tenant_id, slug=slug, name=name, is_builtin=False)
        self._roles.create(role)

        permission_ids = self._roles.get_permission_ids_by_slugs(list(permission_slugs))
        self._roles.replace_permissions(role_id, list(permission_ids.values()))

        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if _constraint_name(exc) in _ROLE_SLUG_CONFLICT_CONSTRAINTS:
                raise RoleAssignmentConflictError(
                    "A role with this slug already exists for this tenant."
                ) from exc
            raise
        except Exception:
            self._session.rollback()
            raise
        return role

    def update_role(
        self,
        tenant_id: UUID,
        role_id: UUID,
        actor_context: AuthorizationContext,
        name: str | None = None,
        permission_slugs: Sequence[str] | None = None,
    ) -> Role:
        """Update a custom role's display name and/or permission set.

        The slug is immutable after creation and cannot be changed here.
        Builtin roles can never be updated (ProtectedRoleError).
        """
        role = self._get_editable_custom_role(tenant_id, role_id)

        if permission_slugs is not None:
            validated_permissions = self._validate_permission_slugs(permission_slugs)
            self._require_subset_delegation(actor_context, validated_permissions)
            permission_ids = self._roles.get_permission_ids_by_slugs(
                list(permission_slugs)
            )
            self._roles.replace_permissions(role_id, list(permission_ids.values()))

        if name is not None:
            role.name = name

        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return role

    def soft_delete_role(self, tenant_id: UUID, role_id: UUID) -> None:
        """Soft-delete a custom role that has no active assignments.

        Rejects deletion while active assignments exist (RoleAssignmentConflictError)
        rather than auto-revoking them: an explicit prior revoke keeps
        privilege loss deliberate and separately audited. Builtin roles can
        never be deleted (ProtectedRoleError).
        """
        role = self._get_editable_custom_role(tenant_id, role_id)

        if self._assignments.count_active_assignments_for_role(role_id) > 0:
            raise RoleAssignmentConflictError(
                "Role has active assignments; revoke them before deleting the role."
            )

        role.deleted_at = datetime.now(timezone.utc)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def assign_role(
        self,
        tenant_id: UUID,
        target_user_id: UUID,
        role_id: UUID,
        assigned_by_user_id: UUID,
        actor_context: AuthorizationContext,
    ) -> UserRoleAssignment:
        """Assign a role to a user within the caller's own tenant.

        Verifies, in order: the target user exists in this tenant and is
        active; the role is active and either builtin or owned by this
        tenant; the caller effectively holds every permission the role
        would grant (subset delegation); no duplicate active grant exists.
        """
        target_user = self._users.get_by_id(tenant_id, target_user_id)
        if target_user is None or target_user.deleted_at is not None:
            raise UserNotFoundError()

        role = self._roles.get_role_for_tenant(tenant_id, role_id)
        if role is None or role.deleted_at is not None:
            raise RoleNotFoundError()

        role_permissions = [
            PermissionSlug(slug)
            for slug in self._roles.get_permission_slugs_for_role(role_id)
        ]
        self._require_subset_delegation(actor_context, role_permissions)

        existing = self._assignments.get_active_assignment_for_tenant(
            tenant_id, target_user_id, role_id
        )
        if existing is not None:
            raise RoleAssignmentConflictError()

        assignment = UserRoleAssignment(
            id=uuid.uuid4(),
            user_id=target_user_id,
            tenant_id=tenant_id,
            role_id=role_id,
            assigned_by_user_id=assigned_by_user_id,
        )
        self._assignments.create(assignment)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            if _constraint_name(exc) == _DUPLICATE_ASSIGNMENT_CONSTRAINT:
                raise RoleAssignmentConflictError() from exc
            raise
        except Exception:
            self._session.rollback()
            raise
        return assignment

    def revoke_assignment(
        self,
        tenant_id: UUID,
        assignment_id: UUID,
        revoked_by_user_id: UUID,
    ) -> None:
        """Revoke a tenant-scoped role assignment, enforcing last-admin protection.

        Lock ordering (see module docstring): the tenant row is locked
        first, the assignment is revalidated as still active (a concurrent
        transaction may have revoked it while this one waited for the
        lock), then the active-admin count is evaluated, then the revoke is
        applied -- all inside this one commit.
        """
        assignment = self._assignments.get_assignment_for_tenant(
            tenant_id, assignment_id
        )
        if assignment is None or assignment.revoked_at is not None:
            raise RoleNotFoundError("Role assignment not found.")

        is_admin_assignment = (
            assignment.role.is_builtin
            and assignment.role.slug == BuiltInRoleSlug.TENANT_ADMIN
        )

        try:
            if is_admin_assignment:
                self._tenants.lock_for_update(tenant_id)
                # Force a fresh read of this specific row: a plain re-select
                # would return the same identity-mapped Python object
                # without refreshing its attributes, masking a concurrent
                # commit that revoked it while we waited for the lock.
                self._session.refresh(assignment)
                if assignment.revoked_at is not None:
                    raise RoleNotFoundError("Role assignment not found.")
                if self._assignments.count_active_tenant_admins(tenant_id) <= 1:
                    raise LastTenantAdminError()

            self._assignments.revoke(
                assignment_id, revoked_by_user_id, datetime.now(timezone.utc)
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

    def _get_editable_custom_role(self, tenant_id: UUID, role_id: UUID) -> Role:
        """Fetch a role tenant-scoped, rejecting missing or builtin roles."""
        role = self._roles.get_role_for_tenant(tenant_id, role_id)
        if role is None:
            raise RoleNotFoundError()
        if role.is_builtin:
            raise ProtectedRoleError()
        return role

    def _require_available_slug(self, tenant_id: UUID, slug: str) -> None:
        """Reject a slug reserved for a builtin role or already used by this tenant."""
        if slug in _RESERVED_BUILTIN_SLUGS:
            raise ProtectedRoleError("Slug is reserved for a built-in role.")
        if self._roles.get_active_custom_role_by_slug(tenant_id, slug) is not None:
            raise RoleAssignmentConflictError(
                "A role with this slug already exists for this tenant."
            )

    def _validate_permission_slugs(
        self, permission_slugs: Sequence[str]
    ) -> list[PermissionSlug]:
        """Validate slugs against the known catalog; the authoritative boundary.

        API-layer enum typing gives early feedback only -- this check runs
        regardless of how this method is called.
        """
        unknown = sorted(set(permission_slugs) - _KNOWN_PERMISSION_SLUGS)
        if unknown:
            raise PermissionNotFoundError(f"Unknown permission slug(s): {unknown}")
        return [PermissionSlug(slug) for slug in permission_slugs]

    def _require_subset_delegation(
        self,
        actor_context: AuthorizationContext,
        permission_slugs: Sequence[PermissionSlug],
    ) -> None:
        """Reject granting permissions the caller does not effectively hold.

        Delegates entirely to AuthorizationContext.has_all, which already
        encodes the super_admin bypass; no bypass logic is duplicated here.
        """
        if permission_slugs and not actor_context.has_all(*permission_slugs):
            raise PermissionDeniedError(
                "Cannot grant permissions beyond what you effectively hold."
            )

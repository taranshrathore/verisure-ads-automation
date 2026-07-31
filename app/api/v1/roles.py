"""Role management API endpoints: custom roles and role assignments.

All authorization happens via require_permission dependencies declared at
the route signature level -- route bodies contain no permission logic.
Every operation is scoped to context.tenant_id (the caller's own,
authenticated tenant), never a tenant_id taken from the request.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.authorization import require_permission
from app.api.dependencies import get_current_user, get_role_management_service
from app.core.authorization.catalog import PermissionSlug
from app.core.authorization.context import AuthorizationContext
from app.models.role import Role
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment
from app.services.role_management_service import RoleManagementService

router = APIRouter(tags=["roles"])


class RoleRead(BaseModel):
    """Response body describing a role and its attached permissions."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID | None
    slug: str
    name: str
    is_builtin: bool
    permission_slugs: list[str]


class RoleCreateRequest(BaseModel):
    """Request body for POST /roles."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    permission_slugs: list[PermissionSlug] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    """Request body for PATCH /roles/{role_id}.

    Slug is deliberately absent: it is immutable after creation.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    permission_slugs: list[PermissionSlug] | None = None


class RoleAssignmentCreateRequest(BaseModel):
    """Request body for POST /users/{user_id}/roles."""

    model_config = ConfigDict(extra="forbid")

    role_id: UUID


class RoleAssignmentRead(BaseModel):
    """Response body describing a role assignment."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    user_id: UUID
    tenant_id: UUID
    role_id: UUID
    assigned_by_user_id: UUID | None
    revoked_at: datetime | None


def _to_role_read(
    role: Role, role_management_service: RoleManagementService
) -> RoleRead:
    """Build a RoleRead response, fetching the role's current permission set."""
    return RoleRead(
        id=role.id,
        tenant_id=role.tenant_id,
        slug=role.slug,
        name=role.name,
        is_builtin=role.is_builtin,
        permission_slugs=role_management_service.get_permission_slugs(role.id),
    )


def _to_assignment_read(assignment: UserRoleAssignment) -> RoleAssignmentRead:
    """Build a RoleAssignmentRead response from an ORM assignment row."""
    return RoleAssignmentRead(
        id=assignment.id,
        user_id=assignment.user_id,
        tenant_id=assignment.tenant_id,
        role_id=assignment.role_id,
        assigned_by_user_id=assignment.assigned_by_user_id,
        revoked_at=assignment.revoked_at,
    )


@router.get(
    "/roles",
    status_code=status.HTTP_200_OK,
    response_model=list[RoleRead],
    summary="List roles visible to the current tenant",
    description="Returns built-in roles plus the tenant's own active custom roles.",
)
def list_roles(
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_READ)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> list[RoleRead]:
    """List roles visible to the caller's tenant."""
    roles = role_management_service.list_roles(context.tenant_id)
    return [_to_role_read(role, role_management_service) for role in roles]


@router.post(
    "/roles",
    status_code=status.HTTP_201_CREATED,
    response_model=RoleRead,
    summary="Create a tenant-owned custom role",
    description="Rejects reserved built-in slugs, duplicate tenant slugs, unknown "
    "permission slugs, and permissions the caller does not effectively hold.",
)
def create_role(
    payload: RoleCreateRequest,
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_MANAGE)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> RoleRead:
    """Create a tenant-owned custom role."""
    role = role_management_service.create_custom_role(
        tenant_id=context.tenant_id,
        slug=payload.slug,
        name=payload.name,
        permission_slugs=[permission.value for permission in payload.permission_slugs],
        actor_context=context,
    )
    return _to_role_read(role, role_management_service)


@router.patch(
    "/roles/{role_id}",
    status_code=status.HTTP_200_OK,
    response_model=RoleRead,
    summary="Update a tenant-owned custom role",
    description="Updates display name and/or permission set. Slug is immutable. "
    "Built-in roles cannot be updated.",
)
def update_role(
    role_id: UUID,
    payload: RoleUpdateRequest,
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_MANAGE)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> RoleRead:
    """Update a tenant-owned custom role's name and/or permission set."""
    permission_slugs = (
        [permission.value for permission in payload.permission_slugs]
        if payload.permission_slugs is not None
        else None
    )
    role = role_management_service.update_role(
        tenant_id=context.tenant_id,
        role_id=role_id,
        actor_context=context,
        name=payload.name,
        permission_slugs=permission_slugs,
    )
    return _to_role_read(role, role_management_service)


@router.delete(
    "/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a tenant-owned custom role",
    description="Rejects deletion while active assignments exist. Built-in roles "
    "cannot be deleted.",
)
def delete_role(
    role_id: UUID,
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_MANAGE)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> Response:
    """Soft-delete a tenant-owned custom role."""
    role_management_service.soft_delete_role(context.tenant_id, role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/users/{user_id}/roles",
    status_code=status.HTTP_201_CREATED,
    response_model=RoleAssignmentRead,
    summary="Assign a role to a user within the caller's own tenant",
    description="Rejects cross-tenant targets, inactive users/roles, duplicate "
    "active grants, and role permission sets beyond what the caller holds.",
)
def assign_role(
    user_id: UUID,
    payload: RoleAssignmentCreateRequest,
    current_user: User = Depends(get_current_user),
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_MANAGE)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> RoleAssignmentRead:
    """Assign a role to a user within the caller's own tenant."""
    assignment = role_management_service.assign_role(
        tenant_id=context.tenant_id,
        target_user_id=user_id,
        role_id=payload.role_id,
        assigned_by_user_id=current_user.id,
        actor_context=context,
    )
    return _to_assignment_read(assignment)


@router.delete(
    "/role-assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a role assignment within the caller's own tenant",
    description="A flat assignment-resource route: the assignment id alone "
    "resolves the tenant-scoped row, avoiding a path/body user-mismatch. "
    "Enforces last-tenant-administrator protection.",
)
def revoke_role_assignment(
    assignment_id: UUID,
    current_user: User = Depends(get_current_user),
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.ROLES_MANAGE)
    ),
    role_management_service: RoleManagementService = Depends(
        get_role_management_service
    ),
) -> Response:
    """Revoke a role assignment within the caller's own tenant."""
    role_management_service.revoke_assignment(
        tenant_id=context.tenant_id,
        assignment_id=assignment_id,
        revoked_by_user_id=current_user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

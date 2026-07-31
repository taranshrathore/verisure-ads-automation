"""Service-level tests for SystemRoleManagementService.

Internal-only service (no HTTP endpoint this milestone); exercised
directly against the real (test) database via db_session.
"""

import uuid

import pytest

from app.core.authorization.catalog import SystemRoleSlug
from app.core.exceptions import (
    PlatformTenantRequiredError,
    RoleAssignmentConflictError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.models.user import User
from app.repositories.system_role_assignment_repository import (
    SystemRoleAssignmentRepository,
)
from app.repositories.user_repository import UserRepository
from app.services.system_role_management_service import SystemRoleManagementService


def _make_service(db_session) -> SystemRoleManagementService:
    return SystemRoleManagementService(
        system_role_assignment_repository=SystemRoleAssignmentRepository(db_session),
        user_repository=UserRepository(db_session),
        session=db_session,
    )


def test_assign_system_role_rejects_non_platform_tenant_user(
    db_session, authorization_fixture
) -> None:
    user, _ = authorization_fixture()  # ordinary customer tenant
    service = _make_service(db_session)

    with pytest.raises(PlatformTenantRequiredError):
        service.assign_system_role(user.id, SystemRoleSlug.SUPER_ADMIN)


def test_assign_system_role_rejects_unknown_user(db_session) -> None:
    service = _make_service(db_session)

    with pytest.raises(UserNotFoundError):
        service.assign_system_role(uuid.uuid4(), SystemRoleSlug.SUPER_ADMIN)


def test_assign_system_role_succeeds_for_platform_tenant_user(
    db_session, authorization_fixture
) -> None:
    platform_user, _ = authorization_fixture(system_role="super_admin")
    second_platform_user = User(
        tenant_id=platform_user.tenant_id,
        email=f"platform-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(second_platform_user)
    db_session.flush()
    service = _make_service(db_session)

    assignment = service.assign_system_role(
        second_platform_user.id, SystemRoleSlug.SUPER_ADMIN
    )

    assert assignment.system_role == SystemRoleSlug.SUPER_ADMIN.value


def test_assign_system_role_rejects_duplicate_active_assignment(
    db_session, authorization_fixture
) -> None:
    platform_user, _ = authorization_fixture(system_role="super_admin")
    service = _make_service(db_session)

    with pytest.raises(RoleAssignmentConflictError):
        service.assign_system_role(platform_user.id, SystemRoleSlug.SUPER_ADMIN)


def test_revoke_system_role_rejects_unknown_assignment(db_session) -> None:
    service = _make_service(db_session)

    with pytest.raises(RoleNotFoundError):
        service.revoke_system_role(uuid.uuid4())


def test_revoke_system_role_succeeds(db_session, authorization_fixture) -> None:
    platform_user, _ = authorization_fixture(system_role="super_admin")
    repository = SystemRoleAssignmentRepository(db_session)
    assignment = repository.get_active_assignment(
        platform_user.id, SystemRoleSlug.SUPER_ADMIN.value
    )
    service = _make_service(db_session)

    service.revoke_system_role(assignment.id)

    assert (
        repository.get_active_assignment(
            platform_user.id, SystemRoleSlug.SUPER_ADMIN.value
        )
        is None
    )

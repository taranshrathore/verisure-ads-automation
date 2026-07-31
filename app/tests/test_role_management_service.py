"""Service-level tests for RoleManagementService.

Exercises the service directly against the real (test) database via
db_session, using authorization_fixture and authorization_context_factory
for setup. No mocking of authorization or persistence: repositories and
the service run their real implementation against the rolled-back test
transaction, exactly as in production.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization.catalog import PermissionSlug
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
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment
from app.repositories.role_repository import RoleRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.repositories.user_role_assignment_repository import (
    UserRoleAssignmentRepository,
)
from app.services.role_management_service import RoleManagementService


def _make_service(db_session: Session) -> RoleManagementService:
    return RoleManagementService(
        role_repository=RoleRepository(db_session),
        user_role_assignment_repository=UserRoleAssignmentRepository(db_session),
        user_repository=UserRepository(db_session),
        tenant_repository=TenantRepository(db_session),
        session=db_session,
    )


def _builtin_role(db_session: Session, slug: str) -> Role:
    return (
        db_session.query(Role)
        .filter(Role.tenant_id.is_(None), Role.slug == slug)
        .one()
    )


def _add_user(db_session: Session, tenant: Tenant, *, deleted: bool = False) -> User:
    user = User(
        tenant_id=tenant.id,
        email=f"role-mgmt-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _add_tenant_admin_assignment(db_session: Session, tenant: Tenant, user: User) -> None:
    role = _builtin_role(db_session, "tenant_admin")
    db_session.add(
        UserRoleAssignment(user_id=user.id, tenant_id=tenant.id, role_id=role.id)
    )
    db_session.flush()


# ---------------------------------------------------------------------------
# create_custom_role
# ---------------------------------------------------------------------------


def test_create_custom_role_success(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    role = service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="reporter",
        name="Reporter",
        permission_slugs=[PermissionSlug.CAMPAIGNS_READ.value],
        actor_context=context,
    )

    assert role.slug == "reporter"
    assert role.is_builtin is False
    assert service.get_permission_slugs(role.id) == [PermissionSlug.CAMPAIGNS_READ.value]


def test_create_custom_role_rejects_reserved_slug(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    with pytest.raises(ProtectedRoleError):
        service.create_custom_role(
            tenant_id=admin.tenant_id,
            slug="tenant_admin",
            name="Fake Admin",
            permission_slugs=[],
            actor_context=context,
        )


def test_create_custom_role_rejects_duplicate_tenant_slug(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="reporter",
        name="Reporter",
        permission_slugs=[],
        actor_context=context,
    )
    with pytest.raises(RoleAssignmentConflictError):
        service.create_custom_role(
            tenant_id=admin.tenant_id,
            slug="reporter",
            name="Reporter Two",
            permission_slugs=[],
            actor_context=context,
        )


def test_create_custom_role_rejects_unknown_permission(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    with pytest.raises(PermissionNotFoundError):
        service.create_custom_role(
            tenant_id=admin.tenant_id,
            slug="reporter",
            name="Reporter",
            permission_slugs=["campaigns:teleport"],
            actor_context=context,
        )


def test_create_custom_role_rejects_subset_delegation_violation_with_no_partial_state(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    viewer, _ = authorization_fixture(role_slug="viewer")  # holds only campaigns:read
    context = authorization_context_factory(viewer)
    service = _make_service(db_session)

    with pytest.raises(PermissionDeniedError):
        service.create_custom_role(
            tenant_id=viewer.tenant_id,
            slug="manager-clone",
            name="Manager Clone",
            permission_slugs=[PermissionSlug.CAMPAIGNS_MANAGE.value],
            actor_context=context,
        )

    role_repository = RoleRepository(db_session)
    assert (
        role_repository.get_active_custom_role_by_slug(viewer.tenant_id, "manager-clone")
        is None
    )


def test_create_custom_role_allows_tenant_admin_to_grant_any_known_permission(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    role = service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="full-clone",
        name="Full Clone",
        permission_slugs=[permission.value for permission in PermissionSlug],
        actor_context=context,
    )
    assert set(service.get_permission_slugs(role.id)) == {
        permission.value for permission in PermissionSlug
    }


def test_create_custom_role_reraises_unrelated_integrity_error(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    """An IntegrityError unrelated to slug uniqueness must not be mislabeled
    as RoleAssignmentConflictError. name=None violates roles.name's NOT NULL
    constraint -- a different constraint than the slug-uniqueness indexes
    this service knows how to translate -- so it must propagate as-is.
    """
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    with pytest.raises(IntegrityError):
        service.create_custom_role(
            tenant_id=admin.tenant_id,
            slug="unrelated-integrity-error",
            name=None,  # type: ignore[arg-type]
            permission_slugs=[],
            actor_context=context,
        )


# ---------------------------------------------------------------------------
# update_role / soft_delete_role
# ---------------------------------------------------------------------------


def test_update_role_rejects_builtin(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    builtin_viewer = _builtin_role(db_session, "viewer")

    with pytest.raises(ProtectedRoleError):
        service.update_role(
            tenant_id=admin.tenant_id,
            role_id=builtin_viewer.id,
            actor_context=context,
            name="Hacked Viewer",
        )


def test_update_role_changes_name_and_permissions(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    role = service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="reporter",
        name="Reporter",
        permission_slugs=[PermissionSlug.CAMPAIGNS_READ.value],
        actor_context=context,
    )
    updated = service.update_role(
        tenant_id=admin.tenant_id,
        role_id=role.id,
        actor_context=context,
        name="Senior Reporter",
        permission_slugs=[PermissionSlug.CAMPAIGNS_MANAGE.value],
    )

    assert updated.name == "Senior Reporter"
    assert service.get_permission_slugs(role.id) == [PermissionSlug.CAMPAIGNS_MANAGE.value]


def test_soft_delete_role_rejects_builtin(db_session, authorization_fixture) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    service = _make_service(db_session)
    builtin_viewer = _builtin_role(db_session, "viewer")

    with pytest.raises(ProtectedRoleError):
        service.soft_delete_role(admin.tenant_id, builtin_viewer.id)


def test_soft_delete_role_rejects_role_with_active_assignments_then_succeeds_after_revoke(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    role = service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="reporter",
        name="Reporter",
        permission_slugs=[PermissionSlug.CAMPAIGNS_READ.value],
        actor_context=context,
    )
    service.assign_role(
        tenant_id=admin.tenant_id,
        target_user_id=admin.id,
        role_id=role.id,
        assigned_by_user_id=admin.id,
        actor_context=context,
    )

    with pytest.raises(RoleAssignmentConflictError):
        service.soft_delete_role(admin.tenant_id, role.id)

    assignment_repository = UserRoleAssignmentRepository(db_session)
    assignment = assignment_repository.get_active_assignment_for_tenant(
        admin.tenant_id, admin.id, role.id
    )
    service.revoke_assignment(admin.tenant_id, assignment.id, admin.id)

    service.soft_delete_role(admin.tenant_id, role.id)  # no longer raises


# ---------------------------------------------------------------------------
# assign_role
# ---------------------------------------------------------------------------


def test_assign_role_rejects_user_from_another_tenant(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    other_tenant_user, _ = authorization_fixture()
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    viewer_role = _builtin_role(db_session, "viewer")

    with pytest.raises(UserNotFoundError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=other_tenant_user.id,
            role_id=viewer_role.id,
            assigned_by_user_id=admin.id,
            actor_context=context,
        )


def test_assign_role_rejects_soft_deleted_target_user(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    viewer_role = _builtin_role(db_session, "viewer")
    deleted_user = _add_user(db_session, admin.tenant, deleted=True)

    with pytest.raises(UserNotFoundError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=deleted_user.id,
            role_id=viewer_role.id,
            assigned_by_user_id=admin.id,
            actor_context=context,
        )


def test_assign_role_rejects_role_owned_by_another_tenant(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    other_admin, _ = authorization_fixture(role_slug="tenant_admin")
    other_context = authorization_context_factory(other_admin)
    other_service = _make_service(db_session)
    foreign_role = other_service.create_custom_role(
        tenant_id=other_admin.tenant_id,
        slug="foreign",
        name="Foreign",
        permission_slugs=[],
        actor_context=other_context,
    )

    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    with pytest.raises(RoleNotFoundError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=admin.id,
            role_id=foreign_role.id,
            assigned_by_user_id=admin.id,
            actor_context=context,
        )


def test_assign_role_rejects_soft_deleted_role(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)

    role = service.create_custom_role(
        tenant_id=admin.tenant_id,
        slug="temp",
        name="Temp",
        permission_slugs=[],
        actor_context=context,
    )
    service.soft_delete_role(admin.tenant_id, role.id)

    with pytest.raises(RoleNotFoundError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=admin.id,
            role_id=role.id,
            assigned_by_user_id=admin.id,
            actor_context=context,
        )


def test_assign_role_rejects_duplicate_active_assignment(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    viewer_role = _builtin_role(db_session, "viewer")
    target = _add_user(db_session, admin.tenant)

    service.assign_role(
        tenant_id=admin.tenant_id,
        target_user_id=target.id,
        role_id=viewer_role.id,
        assigned_by_user_id=admin.id,
        actor_context=context,
    )
    with pytest.raises(RoleAssignmentConflictError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=target.id,
            role_id=viewer_role.id,
            assigned_by_user_id=admin.id,
            actor_context=context,
        )


def test_assign_role_rejects_subset_delegation_violation(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    # manager holds users:read, roles:read, campaigns:read, campaigns:manage
    # -- notably NOT roles:manage, but this exercises the same has_all guard
    # used for a role that grants more than the actor holds (campaigns:manage
    # is held; users:manage and roles:manage are not, and tenant_admin grants
    # both).
    manager, _ = authorization_fixture(role_slug="manager")
    context = authorization_context_factory(manager)
    service = _make_service(db_session)
    admin_role = _builtin_role(db_session, "tenant_admin")
    target = _add_user(db_session, manager.tenant)

    with pytest.raises(PermissionDeniedError):
        service.assign_role(
            tenant_id=manager.tenant_id,
            target_user_id=target.id,
            role_id=admin_role.id,
            assigned_by_user_id=manager.id,
            actor_context=context,
        )


def test_assign_role_allows_tenant_admin_to_assign_tenant_admin(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    admin_role = _builtin_role(db_session, "tenant_admin")
    target = _add_user(db_session, admin.tenant)

    assignment = service.assign_role(
        tenant_id=admin.tenant_id,
        target_user_id=target.id,
        role_id=admin_role.id,
        assigned_by_user_id=admin.id,
        actor_context=context,
    )

    assert assignment.role_id == admin_role.id


def test_assign_role_reraises_unrelated_integrity_error(
    db_session, authorization_fixture, authorization_context_factory
) -> None:
    """An IntegrityError unrelated to the duplicate-active-assignment index
    must not be mislabeled as RoleAssignmentConflictError. A nonexistent
    assigned_by_user_id violates a foreign-key constraint -- a different
    constraint than the one this service knows how to translate -- so it
    must propagate as-is.
    """
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    context = authorization_context_factory(admin)
    service = _make_service(db_session)
    viewer_role = _builtin_role(db_session, "viewer")
    target = _add_user(db_session, admin.tenant)

    with pytest.raises(IntegrityError):
        service.assign_role(
            tenant_id=admin.tenant_id,
            target_user_id=target.id,
            role_id=viewer_role.id,
            assigned_by_user_id=uuid.uuid4(),  # no such user -> FK violation
            actor_context=context,
        )


# ---------------------------------------------------------------------------
# revoke_assignment / last-admin protection
# ---------------------------------------------------------------------------


def test_revoke_assignment_rejects_unknown_or_cross_tenant(
    db_session, authorization_fixture
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    service = _make_service(db_session)

    with pytest.raises(RoleNotFoundError):
        service.revoke_assignment(admin.tenant_id, uuid.uuid4(), admin.id)


def test_revoke_assignment_denies_removing_last_tenant_admin_with_no_partial_state(
    db_session, authorization_fixture
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    # Commit the fixture setup first so it is a durable checkpoint, exactly
    # as it would already be committed by an earlier, separate request in
    # production before this revoke request arrives. Without this, the
    # service's own internal rollback (below) would roll back to the start
    # of this test's savepoint-backed transaction and wipe the fixture's
    # still-uncommitted setup too, which is a test-isolation artifact, not
    # a production scenario (a fresh Session per request never straddles
    # both the setup and the revoke).
    db_session.commit()

    service = _make_service(db_session)
    assignment_repository = UserRoleAssignmentRepository(db_session)
    admin_role = _builtin_role(db_session, "tenant_admin")
    assignment = assignment_repository.get_active_assignment_for_tenant(
        admin.tenant_id, admin.id, admin_role.id
    )

    with pytest.raises(LastTenantAdminError):
        service.revoke_assignment(admin.tenant_id, assignment.id, admin.id)

    still_active = assignment_repository.get_active_assignment_for_tenant(
        admin.tenant_id, admin.id, admin_role.id
    )
    assert still_active is not None


def test_revoke_assignment_succeeds_when_second_admin_exists(
    db_session, authorization_fixture
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    service = _make_service(db_session)
    assignment_repository = UserRoleAssignmentRepository(db_session)
    admin_role = _builtin_role(db_session, "tenant_admin")

    second_admin = _add_user(db_session, admin.tenant)
    _add_tenant_admin_assignment(db_session, admin.tenant, second_admin)

    assignment = assignment_repository.get_active_assignment_for_tenant(
        admin.tenant_id, admin.id, admin_role.id
    )
    service.revoke_assignment(admin.tenant_id, assignment.id, second_admin.id)

    assert assignment_repository.count_active_tenant_admins(admin.tenant_id) == 1


def test_revoke_assignment_excludes_soft_deleted_admins_from_last_admin_count(
    db_session, authorization_fixture
) -> None:
    admin, _ = authorization_fixture(role_slug="tenant_admin")
    service = _make_service(db_session)
    assignment_repository = UserRoleAssignmentRepository(db_session)
    admin_role = _builtin_role(db_session, "tenant_admin")

    deleted_admin = _add_user(db_session, admin.tenant, deleted=True)
    _add_tenant_admin_assignment(db_session, admin.tenant, deleted_admin)

    assignment = assignment_repository.get_active_assignment_for_tenant(
        admin.tenant_id, admin.id, admin_role.id
    )
    with pytest.raises(LastTenantAdminError):
        service.revoke_assignment(admin.tenant_id, assignment.id, admin.id)

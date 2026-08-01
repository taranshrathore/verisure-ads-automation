"""remove local rbac tables

Revision ID: c4d8f1a9b6e3
Revises: b7c3e5a9d214
Create Date: 2026-08-01 12:40:00.000000

VeriSure CRM is intended to become the single source of truth for roles
and permissions (see docs/HANDOFF.md for the migration status and the
CRM integration contract still missing from this repository). This
migration removes the local RBAC schema introduced by fd90462691b5 (add
rbac tables) and seeded by b7c3e5a9d214 (seed rbac catalog): the
application code that read/wrote these tables has already been removed,
so they are dropped outright rather than merely deprecated.

Drops, in reverse dependency order (children referencing roles/permissions/
users before the tables they reference):
    1. system_role_assignments (FK -> users)
    2. user_role_assignments (composite FK -> users(id, tenant_id); FK -> roles)
    3. role_permissions (FK -> roles, permissions)
    4. roles (FK -> tenants)
    5. permissions
    6. uq_users_id_tenant_id on users -- this composite unique constraint
       existed solely to be the referenced side of user_role_assignments'
       composite FK (see fd90462691b5's own comment); nothing else
       references it, so it is dropped too.

tenants, users, and refresh_tokens (and all their data) are untouched.
users.role (the pre-RBAC flat string column) is untouched: it predates
this RBAC phase, nothing here depends on it, and whether it is still
needed is a decision for the CRM integration work, not this migration.

downgrade() fully recreates the dropped schema (matching fd90462691b5's
upgrade()) and re-seeds the frozen catalog (matching b7c3e5a9d214's
upgrade()), since upgrade() here destroys both schema and data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8f1a9b6e3'
down_revision: Union[str, Sequence[str], None] = 'b7c3e5a9d214'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen snapshots, identical to b7c3e5a9d214, needed to recreate seed data
# on downgrade.
_PERMISSIONS: list[tuple[str, str, str]] = [
    ('7d3f9b2a-4c81-4e6a-9f27-3b5a8c1d0e42', 'users:read',
     'View users belonging to the tenant.'),
    ('2a6e4d91-b7c3-4f28-8a54-6d1e9f0b3c75', 'users:manage',
     'Invite, update, and deactivate tenant users.'),
    ('91c5b8e3-2f6d-4a17-b9e0-4c7a2d8f5b16', 'roles:read',
     'View roles and their permissions.'),
    ('e4a7d2c9-8b15-4d63-a2f8-7e0c5b9d3a48', 'roles:manage',
     'Create, update, assign, and revoke roles.'),
    ('5b9e1f74-3d2c-4b8a-8c61-9a4e7f2d0b53', 'campaigns:read',
     'View advertising campaigns.'),
    ('c8d24a6f-7e91-4c35-b7d2-1f8e6a3c9b04', 'campaigns:manage',
     'Create, update, and delete advertising campaigns.'),
]

_BUILTIN_ROLES: list[tuple[str, str, str]] = [
    ('3f7b5c28-9a4d-4e12-8b6f-5c2d9e7a1f83', 'tenant_admin', 'Tenant Administrator'),
    ('a2c86e15-4f7b-4a93-9d38-8b1c4e6f2a97', 'manager', 'Manager'),
    ('68f3d9b7-1c5e-4d84-ba29-3e7f0a5c8d61', 'employee', 'Employee'),
    ('d15a7e93-6b2f-4c58-9e47-2a8d5f1b7c30', 'viewer', 'Viewer'),
]

_BUILTIN_ROLE_PERMISSIONS: dict[str, list[str]] = {
    'tenant_admin': [
        'users:read', 'users:manage',
        'roles:read', 'roles:manage',
        'campaigns:read', 'campaigns:manage',
    ],
    'manager': [
        'users:read', 'roles:read',
        'campaigns:read', 'campaigns:manage',
    ],
    'employee': [
        'campaigns:read', 'campaigns:manage',
    ],
    'viewer': [
        'campaigns:read',
    ],
}


def upgrade() -> None:
    """Drop the local RBAC tables and their supporting constraint on users."""
    op.drop_index('uq_system_role_assignments_user_id_system_role_active', table_name='system_role_assignments')
    op.drop_index(op.f('ix_system_role_assignments_revoked_by_user_id'), table_name='system_role_assignments')
    op.drop_index(op.f('ix_system_role_assignments_assigned_by_user_id'), table_name='system_role_assignments')
    op.drop_index(op.f('ix_system_role_assignments_user_id'), table_name='system_role_assignments')
    op.drop_table('system_role_assignments')

    op.drop_index('ix_user_role_assignments_user_id_tenant_id_active', table_name='user_role_assignments')
    op.drop_index('uq_user_role_assignments_user_id_role_id_active', table_name='user_role_assignments')
    op.drop_index(op.f('ix_user_role_assignments_revoked_by_user_id'), table_name='user_role_assignments')
    op.drop_index(op.f('ix_user_role_assignments_assigned_by_user_id'), table_name='user_role_assignments')
    op.drop_index(op.f('ix_user_role_assignments_role_id'), table_name='user_role_assignments')
    op.drop_table('user_role_assignments')

    op.drop_index(op.f('ix_role_permissions_permission_id'), table_name='role_permissions')
    op.drop_table('role_permissions')

    op.drop_index('uq_roles_tenant_id_slug_custom', table_name='roles')
    op.drop_index('uq_roles_slug_builtin', table_name='roles')
    op.drop_index(op.f('ix_roles_tenant_id'), table_name='roles')
    op.drop_table('roles')

    op.drop_index(op.f('ix_permissions_slug'), table_name='permissions')
    op.drop_table('permissions')

    op.drop_constraint('uq_users_id_tenant_id', 'users', type_='unique')


def downgrade() -> None:
    """Recreate the RBAC schema and re-seed the frozen catalog."""
    op.create_unique_constraint(
        'uq_users_id_tenant_id', 'users', ['id', 'tenant_id']
    )

    op.create_table(
        'permissions',
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_permissions_slug'),
    )
    op.create_index(op.f('ix_permissions_slug'), 'permissions', ['slug'], unique=False)

    op.create_table(
        'roles',
        sa.Column('tenant_id', sa.UUID(), nullable=True),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_builtin', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "tenant_id IS NULL OR slug NOT IN ('tenant_admin', 'manager', 'employee', 'viewer')",
            name='ck_roles_reserved_slug',
        ),
        sa.CheckConstraint(
            "(tenant_id IS NULL AND is_builtin IS TRUE) OR "
            "(tenant_id IS NOT NULL AND is_builtin IS FALSE)",
            name='ck_roles_scope_matches_builtin',
        ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_roles_tenant_id_tenants'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_roles_tenant_id'), 'roles', ['tenant_id'], unique=False)
    op.create_index(
        'uq_roles_slug_builtin', 'roles', ['slug'],
        unique=True, postgresql_where=sa.text('tenant_id IS NULL'),
    )
    op.create_index(
        'uq_roles_tenant_id_slug_custom', 'roles', ['tenant_id', 'slug'],
        unique=True, postgresql_where=sa.text('tenant_id IS NOT NULL AND deleted_at IS NULL'),
    )

    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('permission_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name='fk_role_permissions_permission_id_permissions'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name='fk_role_permissions_role_id_roles'),
        sa.PrimaryKeyConstraint('role_id', 'permission_id'),
    )
    op.create_index(op.f('ix_role_permissions_permission_id'), 'role_permissions', ['permission_id'], unique=False)

    op.create_table(
        'user_role_assignments',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('assigned_by_user_id', sa.UUID(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id', 'tenant_id'], ['users.id', 'users.tenant_id'],
            name='fk_user_role_assignments_user_id_tenant_id_users',
        ),
        sa.ForeignKeyConstraint(['assigned_by_user_id'], ['users.id'], name='fk_user_role_assignments_assigned_by_user_id_users'),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id'], name='fk_user_role_assignments_revoked_by_user_id_users'),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name='fk_user_role_assignments_role_id_roles'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_user_role_assignments_role_id'), 'user_role_assignments', ['role_id'], unique=False)
    op.create_index(op.f('ix_user_role_assignments_assigned_by_user_id'), 'user_role_assignments', ['assigned_by_user_id'], unique=False)
    op.create_index(op.f('ix_user_role_assignments_revoked_by_user_id'), 'user_role_assignments', ['revoked_by_user_id'], unique=False)
    op.create_index(
        'uq_user_role_assignments_user_id_role_id_active',
        'user_role_assignments', ['user_id', 'role_id'],
        unique=True, postgresql_where=sa.text('revoked_at IS NULL'),
    )
    op.create_index(
        'ix_user_role_assignments_user_id_tenant_id_active',
        'user_role_assignments', ['user_id', 'tenant_id'],
        unique=False, postgresql_where=sa.text('revoked_at IS NULL'),
    )

    op.create_table(
        'system_role_assignments',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('system_role', sa.String(length=50), nullable=False),
        sa.Column('assigned_by_user_id', sa.UUID(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("system_role IN ('super_admin')", name='ck_system_role_assignments_system_role'),
        sa.ForeignKeyConstraint(['assigned_by_user_id'], ['users.id'], name='fk_system_role_assignments_assigned_by_user_id_users'),
        sa.ForeignKeyConstraint(['revoked_by_user_id'], ['users.id'], name='fk_system_role_assignments_revoked_by_user_id_users'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_system_role_assignments_user_id_users'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_system_role_assignments_user_id'), 'system_role_assignments', ['user_id'], unique=False)
    op.create_index(op.f('ix_system_role_assignments_assigned_by_user_id'), 'system_role_assignments', ['assigned_by_user_id'], unique=False)
    op.create_index(op.f('ix_system_role_assignments_revoked_by_user_id'), 'system_role_assignments', ['revoked_by_user_id'], unique=False)
    op.create_index(
        'uq_system_role_assignments_user_id_system_role_active',
        'system_role_assignments', ['user_id', 'system_role'],
        unique=True, postgresql_where=sa.text('revoked_at IS NULL'),
    )

    connection = op.get_bind()

    for permission_id, slug, description in _PERMISSIONS:
        connection.execute(
            sa.text(
                "INSERT INTO permissions (id, slug, description) "
                "VALUES (:id, :slug, :description) "
                "ON CONFLICT (slug) DO NOTHING"
            ),
            {"id": permission_id, "slug": slug, "description": description},
        )

    for role_id, slug, name in _BUILTIN_ROLES:
        connection.execute(
            sa.text(
                "INSERT INTO roles (id, tenant_id, slug, name, is_builtin) "
                "VALUES (:id, NULL, :slug, :name, TRUE) "
                "ON CONFLICT (slug) WHERE tenant_id IS NULL DO NOTHING"
            ),
            {"id": role_id, "slug": slug, "name": name},
        )

    for role_slug, permission_slugs in _BUILTIN_ROLE_PERMISSIONS.items():
        for permission_slug in permission_slugs:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "SELECT r.id, p.id "
                    "FROM roles r, permissions p "
                    "WHERE r.tenant_id IS NULL AND r.slug = :role_slug "
                    "AND p.slug = :permission_slug "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"role_slug": role_slug, "permission_slug": permission_slug},
            )

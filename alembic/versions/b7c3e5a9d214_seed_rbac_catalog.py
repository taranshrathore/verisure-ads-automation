"""seed rbac catalog

Revision ID: b7c3e5a9d214
Revises: fd90462691b5
Create Date: 2026-07-31 13:10:00.000000

This data migration is a frozen snapshot of the permission catalog and
built-in role definitions at the time it was written. It deliberately does
NOT import from app.core.authorization: an applied migration is immutable
and is not a runtime synchronizer. Future catalog changes ship as new
additive migrations.

Idempotency: all inserts use ON CONFLICT DO NOTHING against the named
unique constraints / partial unique indexes, and role-permission links are
resolved by slug subselects, so re-running against a partially seeded
database is safe.

Seeds no tenants, no users, no credentials, and no assignments.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c3e5a9d214'
down_revision: Union[str, Sequence[str], None] = 'fd90462691b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen snapshot: (id, slug, description)
PERMISSIONS: list[tuple[str, str, str]] = [
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

# Frozen snapshot: (id, slug, display name); tenant_id IS NULL, is_builtin TRUE.
BUILTIN_ROLES: list[tuple[str, str, str]] = [
    ('3f7b5c28-9a4d-4e12-8b6f-5c2d9e7a1f83', 'tenant_admin', 'Tenant Administrator'),
    ('a2c86e15-4f7b-4a93-9d38-8b1c4e6f2a97', 'manager', 'Manager'),
    ('68f3d9b7-1c5e-4d84-ba29-3e7f0a5c8d61', 'employee', 'Employee'),
    ('d15a7e93-6b2f-4c58-9e47-2a8d5f1b7c30', 'viewer', 'Viewer'),
]

# Frozen snapshot: role slug -> permission slugs.
BUILTIN_ROLE_PERMISSIONS: dict[str, list[str]] = {
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

_PERMISSION_SLUGS: list[str] = [slug for _, slug, _ in PERMISSIONS]
_ROLE_SLUGS: list[str] = [slug for _, slug, _ in BUILTIN_ROLES]


def upgrade() -> None:
    """Seed the permission catalog and built-in roles (idempotent)."""
    connection = op.get_bind()

    for permission_id, slug, description in PERMISSIONS:
        connection.execute(
            sa.text(
                "INSERT INTO permissions (id, slug, description) "
                "VALUES (:id, :slug, :description) "
                "ON CONFLICT (slug) DO NOTHING"
            ),
            {"id": permission_id, "slug": slug, "description": description},
        )

    for role_id, slug, name in BUILTIN_ROLES:
        connection.execute(
            sa.text(
                "INSERT INTO roles (id, tenant_id, slug, name, is_builtin) "
                "VALUES (:id, NULL, :slug, :name, TRUE) "
                "ON CONFLICT (slug) WHERE tenant_id IS NULL DO NOTHING"
            ),
            {"id": role_id, "slug": slug, "name": name},
        )

    # Resolve ids by slug (not by the literals above) so linking stays
    # correct even if a row pre-existed with a different id.
    for role_slug, permission_slugs in BUILTIN_ROLE_PERMISSIONS.items():
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


def downgrade() -> None:
    """Remove the seeded catalog in reverse dependency order.

    Also removes assignments and role-permission links that reference the
    seeded rows (including links created later by custom roles referencing
    these permissions), since the foreign keys would otherwise block the
    deletes.
    """
    connection = op.get_bind()

    connection.execute(
        sa.text(
            "DELETE FROM user_role_assignments ura "
            "USING roles r "
            "WHERE ura.role_id = r.id AND r.tenant_id IS NULL "
            "AND r.slug = ANY(:role_slugs)"
        ),
        {"role_slugs": _ROLE_SLUGS},
    )
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions rp "
            "USING roles r "
            "WHERE rp.role_id = r.id AND r.tenant_id IS NULL "
            "AND r.slug = ANY(:role_slugs)"
        ),
        {"role_slugs": _ROLE_SLUGS},
    )
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions rp "
            "USING permissions p "
            "WHERE rp.permission_id = p.id AND p.slug = ANY(:permission_slugs)"
        ),
        {"permission_slugs": _PERMISSION_SLUGS},
    )
    connection.execute(
        sa.text(
            "DELETE FROM roles "
            "WHERE tenant_id IS NULL AND slug = ANY(:role_slugs)"
        ),
        {"role_slugs": _ROLE_SLUGS},
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE slug = ANY(:permission_slugs)"
        ),
        {"permission_slugs": _PERMISSION_SLUGS},
    )

"""ORM model package.

Importing this package registers all models on Base.metadata for Alembic.
"""

from app.models.permission import Permission
from app.models.refresh_token import RefreshToken
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.system_role_assignment import SystemRoleAssignment
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_role_assignment import UserRoleAssignment

__all__ = [
    "Permission",
    "RefreshToken",
    "Role",
    "RolePermission",
    "SystemRoleAssignment",
    "Tenant",
    "User",
    "UserRoleAssignment",
]

"""Framework-agnostic authorization core: typed catalogs and the context."""

from app.core.authorization.catalog import (
    PERMISSION_DESCRIPTIONS,
    PLATFORM_TENANT_SLUG,
    BuiltInRoleSlug,
    PermissionSlug,
    SystemRoleSlug,
)
from app.core.authorization.context import AuthorizationContext

__all__ = [
    "PERMISSION_DESCRIPTIONS",
    "PLATFORM_TENANT_SLUG",
    "AuthorizationContext",
    "BuiltInRoleSlug",
    "PermissionSlug",
    "SystemRoleSlug",
]

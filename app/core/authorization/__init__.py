"""Authorization vocabulary seam.

TEMPORARY / CRM-MIGRATION STATE: this package used to contain a full
database-backed RBAC engine (typed role/system-role catalogs and an
AuthorizationContext read model). That engine has been removed because
VeriSure CRM is intended to become the authoritative source of roles and
permissions. Only PermissionSlug survives, as a stable capability-name
vocabulary with no runtime enforcement behavior -- see catalog.py and
docs/HANDOFF.md for the full migration status and the information still
needed from the CRM team before any real authorization check can be
reintroduced here.
"""

from app.core.authorization.catalog import PERMISSION_DESCRIPTIONS, PermissionSlug

__all__ = [
    "PERMISSION_DESCRIPTIONS",
    "PermissionSlug",
]

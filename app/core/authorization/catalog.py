"""Stable internal capability vocabulary.

TEMPORARY / CRM-MIGRATION STATE: the local RBAC engine (database-backed
roles, permissions, and role assignments) has been removed. VeriSure CRM is
intended to become the single source of truth for authorization, but this
repository does not yet contain the CRM integration contract (token
issuance, claim shape, or permission-lookup API) -- see docs/HANDOFF.md.

``PermissionSlug`` is kept, trimmed to only the capabilities this backend's
own endpoints care about, purely as a stable naming vocabulary: a future
CRM authorization provider can map CRM-supplied permissions onto these
values without this module ever encoding how those permissions are
obtained, cached, or resolved. It performs zero authorization logic by
itself and is not currently read by any endpoint.
"""

from enum import StrEnum


class PermissionSlug(StrEnum):
    """Stable capability names this backend may need once CRM permissions
    are integrated. Not wired to any enforcement path yet.
    """

    CAMPAIGNS_READ = "campaigns:read"
    CAMPAIGNS_MANAGE = "campaigns:manage"


PERMISSION_DESCRIPTIONS: dict[PermissionSlug, str] = {
    PermissionSlug.CAMPAIGNS_READ: "View advertising campaigns.",
    PermissionSlug.CAMPAIGNS_MANAGE: "Create, update, and delete advertising campaigns.",
}

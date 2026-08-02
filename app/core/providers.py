"""Canonical, provider-neutral ad-platform identifier.

This is the single source of truth for which ad platforms this backend
knows about. It is a plain StrEnum with no SQLAlchemy, Pydantic, or
other framework dependency of its own, so core/ code that needs to
know "which provider" (e.g. CredentialEncryptionService) can depend on
it without pulling in the ORM model layer.

Every provider identifier elsewhere in the codebase -- the
CampaignDeployment.provider column type, CampaignSpec.provider,
ProviderAdapterRegistry's keys, etc. -- is this same enum. There is
intentionally no second, duplicate provider enum anywhere in this
project.
"""

from enum import StrEnum


class Provider(StrEnum):
    """An ad platform this backend can (eventually) publish campaigns to."""

    META = "meta"
    GOOGLE = "google"

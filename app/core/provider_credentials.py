"""Provider-neutral credentials value object for adapter publish calls.

Framework-agnostic: no ORM, FastAPI, or SQLAlchemy. Carries opaque
credential bytes plus the Provider they belong to -- never a
provider-specific token shape. Constructed only by PublishCampaignService
from ProviderConnectionService.get_decrypted_credentials(); adapters
receive this object and must never decrypt or fetch credentials themselves.
"""

from dataclasses import dataclass

from app.core.providers import Provider


@dataclass(frozen=True)
class ProviderCredentials:
    """Opaque credentials for one provider, safe to pass into adapters.

    credential_payload is never interpreted here. __repr__ deliberately
    omits the payload so accidental logging cannot leak secrets.
    """

    provider: Provider
    credential_payload: bytes

    def __repr__(self) -> str:
        return (
            f"ProviderCredentials(provider={self.provider!r}, "
            f"credential_payload=<redacted>)"
        )

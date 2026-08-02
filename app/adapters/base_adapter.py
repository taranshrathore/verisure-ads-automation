"""Abstract base interface for platform advertisement adapters.

Declares the provider-neutral adapter contract only. Concrete adapters
still raise NotImplementedError -- no HTTP, OAuth, or network code exists
anywhere in app/adapters/ yet. See app/adapters/meta_adapter.py and
app/adapters/google_adapter.py for the (currently unimplemented) concrete
adapters, and app/adapters/registry.py for how a provider maps to its
adapter instance.

Credentials are injected by PublishCampaignService as ProviderCredentials;
adapters must never fetch, decrypt, or read credentials from the
environment/session/repository layer.
"""

from abc import ABC, abstractmethod

from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec
from app.core.provider_credentials import ProviderCredentials


class BaseAdapter(ABC):
    """Contract for platform-specific advertisement adapters.

    A concrete adapter (MetaAdapter, GoogleAdapter, ...) is the only
    code in this application ever allowed to know a specific provider's
    API shape. Callers (e.g. PublishCampaignService) interact with
    adapters exclusively through this interface, using the
    provider-neutral CampaignSpec, ProviderCredentials, and PublishResult
    types -- never a provider-specific request/response shape.
    """

    @abstractmethod
    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        """Submit a provider-neutral CampaignSpec to this adapter's provider.

        credentials carries opaque decrypted credential bytes for this
        provider; adapters must not fetch or decrypt credentials themselves.

        Returns a PublishResult describing the outcome (success plus
        the provider-assigned external_campaign_id, or failure plus an
        error_message) rather than raising for an ordinary provider
        rejection -- exceptions are reserved for adapter-level failures
        (e.g. not yet implemented, transport failure), not for the
        provider declining the campaign itself.
        """
        raise NotImplementedError

    @abstractmethod
    def pause(self, external_campaign_id: str) -> None:
        """Pause the provider campaign identified by external_campaign_id."""
        raise NotImplementedError

    @abstractmethod
    def resume(self, external_campaign_id: str) -> None:
        """Resume the provider campaign identified by external_campaign_id."""
        raise NotImplementedError

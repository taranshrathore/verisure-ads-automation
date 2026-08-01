"""Abstract base interface for platform advertisement adapters.

MILESTONE 4 SCOPE: this declares the provider-neutral adapter contract
only. No concrete adapter implements any of these methods yet -- no
HTTP, OAuth, or network code exists anywhere in app/adapters/ yet. See
app/adapters/meta_adapter.py and app/adapters/google_adapter.py for the
(currently unimplemented) concrete adapters, and
app/adapters/registry.py for how a provider maps to its adapter
instance.
"""

from abc import ABC, abstractmethod

from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec


class BaseAdapter(ABC):
    """Contract for platform-specific advertisement adapters.

    A concrete adapter (MetaAdapter, GoogleAdapter, ...) is the only
    code in this application ever allowed to know a specific provider's
    API shape. Callers (e.g. a future PublishCampaignService) interact
    with adapters exclusively through this interface, using the
    provider-neutral CampaignSpec and PublishResult types -- never a
    provider-specific request/response shape.
    """

    @abstractmethod
    def publish(self, spec: CampaignSpec) -> PublishResult:
        """Submit a provider-neutral CampaignSpec to this adapter's provider.

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

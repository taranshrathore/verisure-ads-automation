"""Simple provider -> adapter registry.

No dependency-injection framework -- this is a plain class that
instantiates each concrete adapter once and looks it up by provider.
"""

from app.adapters.base_adapter import BaseAdapter
from app.adapters.google_adapter import GoogleAdapter
from app.adapters.meta_adapter import MetaAdapter
from app.models.campaign_deployment import CampaignDeploymentProvider


class ProviderAdapterRegistry:
    """Maps a CampaignDeploymentProvider to its BaseAdapter instance."""

    def __init__(self) -> None:
        self._adapters: dict[CampaignDeploymentProvider, BaseAdapter] = {
            CampaignDeploymentProvider.META: MetaAdapter(),
            CampaignDeploymentProvider.GOOGLE: GoogleAdapter(),
        }

    def get(self, provider: CampaignDeploymentProvider) -> BaseAdapter:
        """Return the adapter instance registered for provider.

        Raises ValueError if provider has no registered adapter.
        """
        try:
            return self._adapters[provider]
        except KeyError:
            raise ValueError(f"No adapter registered for provider: {provider!r}") from None

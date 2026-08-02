"""Simple provider -> adapter registry.

No dependency-injection framework -- this is a plain class that
instantiates each concrete adapter once and looks it up by provider.
"""

from app.adapters.base_adapter import BaseAdapter
from app.adapters.google_adapter import GoogleAdapter
from app.adapters.meta_adapter import MetaAdapter
from app.core.providers import Provider


class ProviderAdapterRegistry:
    """Maps a Provider to its BaseAdapter instance."""

    def __init__(self) -> None:
        self._adapters: dict[Provider, BaseAdapter] = {
            Provider.META: MetaAdapter(),
            Provider.GOOGLE: GoogleAdapter(),
        }

    def get(self, provider: Provider) -> BaseAdapter:
        """Return the adapter instance registered for provider.

        Raises ValueError if provider has no registered adapter.
        """
        try:
            return self._adapters[provider]
        except KeyError:
            raise ValueError(f"No adapter registered for provider: {provider!r}") from None

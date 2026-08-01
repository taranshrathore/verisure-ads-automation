"""Meta (Facebook/Instagram) Ads adapter.

MILESTONE 4 SCOPE: adapter shape only. Every method below raises
NotImplementedError -- there is no OAuth, no Graph API call, and no
HTTP client anywhere in this module yet.

Future implementation will need to:

- Perform OAuth2 (Meta Business Login, or a long-lived system-user
  access token) to obtain a token scoped to the tenant's connected Meta
  ad account, and refresh it before expiry.
- Translate a CampaignSpec into a Meta Marketing API request via the
  Graph API (POST to
  https://graph.facebook.com/<version>/act_<ad_account_id>/campaigns),
  mapping CampaignObjective to Meta's `objective` enum and
  CampaignBudget to `daily_budget`/`lifetime_budget` (Meta expects
  integer minor-currency-unit amounts, not CampaignBudget.amount's
  Decimal directly), then returning a PublishResult built from the
  Graph API response (the created campaign's `id` as
  external_campaign_id on success; the Graph API error's `message` as
  error_message on failure).
- Call the Graph API's campaign update endpoint (POST
  .../<external_campaign_id> with `status=PAUSED`/`status=ACTIVE`) for
  pause/resume, using the stored external_campaign_id.
"""

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec


class MetaAdapter(BaseAdapter):
    """Adapter for the Meta Marketing API. Not implemented yet."""

    def publish(self, spec: CampaignSpec) -> PublishResult:
        """Create a campaign via the Meta Marketing API's Graph API.

        Not implemented: requires OAuth (see module docstring) and a
        Graph API HTTP client, neither of which exist yet.
        """
        raise NotImplementedError("MetaAdapter.publish is not implemented yet.")

    def pause(self, external_campaign_id: str) -> None:
        """Set the Meta campaign's status to PAUSED via the Graph API.

        Not implemented yet.
        """
        raise NotImplementedError("MetaAdapter.pause is not implemented yet.")

    def resume(self, external_campaign_id: str) -> None:
        """Set the Meta campaign's status back to ACTIVE via the Graph API.

        Not implemented yet.
        """
        raise NotImplementedError("MetaAdapter.resume is not implemented yet.")

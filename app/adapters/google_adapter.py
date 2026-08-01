"""Google Ads adapter.

MILESTONE 4 SCOPE: adapter shape only. Every method below raises
NotImplementedError -- there is no OAuth, no Google Ads API call, and
no HTTP/gRPC client anywhere in this module yet.

Future implementation will need to:

- Perform OAuth2 (Google Ads API refresh-token flow: a one-time user
  consent producing a refresh token, exchanged for short-lived access
  tokens on each call) scoped to the tenant's linked Google Ads
  customer account, plus a developer token for API access.
- Translate a CampaignSpec into a Google Ads API CampaignService
  mutate request (CampaignOperation.create), mapping CampaignObjective
  to an AdvertisingChannelType/bidding strategy and CampaignBudget to a
  CampaignBudget resource (Google Ads amounts are micros -- amount *
  1_000_000 -- not CampaignBudget.amount's Decimal directly), then
  returning a PublishResult built from the mutate response (the
  created campaign's resource name/ID as external_campaign_id on
  success; the GoogleAdsException's failure details as error_message
  on failure).
- Call CampaignService.mutateCampaigns with a CampaignOperation.update
  setting status=PAUSED/ENABLED for pause/resume, using the stored
  external_campaign_id.
"""

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec


class GoogleAdapter(BaseAdapter):
    """Adapter for the Google Ads API. Not implemented yet."""

    def publish(self, spec: CampaignSpec) -> PublishResult:
        """Create a campaign via the Google Ads API's CampaignService.

        Not implemented: requires OAuth (see module docstring) and a
        Google Ads API client, neither of which exist yet.
        """
        raise NotImplementedError("GoogleAdapter.publish is not implemented yet.")

    def pause(self, external_campaign_id: str) -> None:
        """Set the Google Ads campaign's status to PAUSED.

        Not implemented yet.
        """
        raise NotImplementedError("GoogleAdapter.pause is not implemented yet.")

    def resume(self, external_campaign_id: str) -> None:
        """Set the Google Ads campaign's status back to ENABLED.

        Not implemented yet.
        """
        raise NotImplementedError("GoogleAdapter.resume is not implemented yet.")

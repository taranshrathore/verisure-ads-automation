"""Builds provider-neutral CampaignSpec objects from ORM rows.

CampaignSpecBuilder is pure domain logic: no network calls, no provider
adapters, no persistence, no Session use of any kind. It only validates
that a Campaign is complete enough to describe an actual ad deployment
and maps it (plus a CampaignDeployment's provider) onto an immutable
CampaignSpec.
"""

from app.core.campaign_spec import CampaignBudget, CampaignSchedule, CampaignSpec
from app.core.exceptions import CampaignValidationError
from app.models.campaign import Campaign
from app.models.campaign_deployment import CampaignDeployment


class CampaignSpecBuilder:
    """Maps a Campaign + CampaignDeployment pair into a CampaignSpec."""

    @staticmethod
    def build(campaign: Campaign, deployment: CampaignDeployment) -> CampaignSpec:
        """Validate completeness and map campaign to an immutable CampaignSpec.

        A draft Campaign is allowed to be incomplete (objective, budget,
        and schedule are all nullable while draft -- see
        app/models/campaign.py), but a CampaignSpec never is: there are
        no sensible defaults to invent for an advertising campaign's
        objective, budget, or run window, so any missing field raises
        CampaignValidationError instead.
        """
        if campaign.objective is None:
            raise CampaignValidationError(
                "Campaign objective is required to build a campaign spec."
            )

        if (
            campaign.budget_type is None
            or campaign.budget_amount is None
            or campaign.currency is None
        ):
            raise CampaignValidationError(
                "Campaign budget is required to build a campaign spec."
            )

        if campaign.start_at is None or campaign.end_at is None:
            raise CampaignValidationError(
                "Campaign schedule is required to build a campaign spec."
            )

        return CampaignSpec(
            campaign_id=campaign.id,
            tenant_id=campaign.tenant_id,
            provider=deployment.provider,
            objective=campaign.objective,
            budget=CampaignBudget(
                type=campaign.budget_type,
                amount=campaign.budget_amount,
                currency=campaign.currency,
            ),
            schedule=CampaignSchedule(
                start_at=campaign.start_at, end_at=campaign.end_at
            ),
        )

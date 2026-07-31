"""Campaign endpoints.

GET /campaigns is the first authorization integration point: it exists to
prove the end-to-end JWT -> CurrentUser -> AuthorizationContext ->
permission-resolution -> endpoint flow before any campaign persistence or
business logic exists. It returns an empty list until a real campaign
model, repository, and service are introduced.
"""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.authorization import require_permission
from app.core.authorization.catalog import PermissionSlug
from app.core.authorization.context import AuthorizationContext

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignListResponse(BaseModel):
    """Placeholder response body for GET /campaigns.

    TODO: Replace `items` with a real CampaignRead schema once campaign
    persistence and business logic exist.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[dict] = Field(default_factory=list)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=CampaignListResponse,
    summary="List campaigns for the current tenant",
    description="First authorization integration point. Requires "
    "campaigns:read. Returns an empty list until campaign persistence "
    "exists; contains no fabricated records.",
)
def list_campaigns(
    context: AuthorizationContext = Depends(
        require_permission(PermissionSlug.CAMPAIGNS_READ)
    ),
) -> CampaignListResponse:
    """Return the (currently empty) list of campaigns visible to the caller."""
    return CampaignListResponse(items=[])

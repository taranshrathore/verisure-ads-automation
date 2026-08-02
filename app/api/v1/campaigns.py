"""Campaign management endpoints.

MILESTONE 1 SCOPE: draft creation/listing/retrieval/editing and
draft-to-archived only -- see app/models/campaign.py and
app/services/campaign_service.py. There is still no "ready" transition.

MILESTONE 6 ADDITION: publish and deployment-listing endpoints wire the
already-built PublishCampaignService/CampaignDeploymentService into
HTTP (see app/services/publish_campaign_service.py). Real Meta/Google
provider adapters still raise NotImplementedError (see app/adapters/),
so today every publish attempt currently ends with FAILED deployments
rather than SUBMITTED/LIVE ones -- this is expected until a future
milestone gives the adapters a real implementation, not a bug in this
wiring. No provider-specific branching exists in this router: both
endpoints below only orchestrate through PublishCampaignService.

AUTHORIZATION STATE: every route below enforces authentication only
(Depends(get_current_user)); there is no local RBAC/permission check.
VeriSure CRM is intended to become the authoritative source of roles and
permissions for this backend, but the integration contract does not yet
exist in this repository -- see docs/HANDOFF.md. No frontend-supplied
role or permission value is read or trusted here or anywhere else in
this backend. Tenant identity is derived exclusively from the
authenticated user's token; it can never appear in a request body.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    get_campaign_service,
    get_current_user,
    get_publish_campaign_service,
)
from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective, CampaignStatus
from app.models.campaign_deployment import (
    CampaignDeployment,
    CampaignDeploymentProvider,
    CampaignDeploymentStatus,
)
from app.models.user import User
from app.services.campaign_service import CampaignService
from app.services.publish_campaign_service import PublishCampaignService

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignCreateRequest(BaseModel):
    """Request body for POST /campaigns.

    Only `name` is required -- a draft may be otherwise incomplete. No
    tenant/creator/status field exists here: those are never
    client-suppliable.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    objective: CampaignObjective | None = None
    budget_type: CampaignBudgetType | None = None
    budget_amount: Decimal | None = None
    currency: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class CampaignUpdateRequest(BaseModel):
    """Request body for PATCH /campaigns/{campaign_id} (draft-only).

    Every field is optional; an omitted field is left untouched, while an
    explicit null clears it (see CampaignService.update_draft). No
    status, tenant_id, or created_by_user_id field exists here -- those
    can never be set by a client.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    objective: CampaignObjective | None = None
    budget_type: CampaignBudgetType | None = None
    budget_amount: Decimal | None = None
    currency: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


class CampaignRead(BaseModel):
    """Response body representing a single campaign."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    objective: CampaignObjective | None
    budget_type: CampaignBudgetType | None
    budget_amount: Decimal | None
    currency: str | None
    start_at: datetime | None
    end_at: datetime | None
    status: CampaignStatus
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, campaign: Campaign) -> "CampaignRead":
        """Build a CampaignRead from a Campaign ORM instance."""
        return cls(
            id=campaign.id,
            name=campaign.name,
            objective=campaign.objective,
            budget_type=campaign.budget_type,
            budget_amount=campaign.budget_amount,
            currency=campaign.currency,
            start_at=campaign.start_at,
            end_at=campaign.end_at,
            status=campaign.status,
            created_by_user_id=campaign.created_by_user_id,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
        )


class CampaignListResponse(BaseModel):
    """Response body for GET /campaigns."""

    model_config = ConfigDict(extra="forbid")

    items: list[CampaignRead] = Field(default_factory=list)


class CampaignDeploymentRead(BaseModel):
    """Response body representing a single campaign deployment.

    Deliberately omits idempotency_key: it is an internal
    duplicate-publish safeguard (see
    app/services/campaign_deployment_service.py), not something a
    client has a proven need to read.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    campaign_id: UUID
    provider: CampaignDeploymentProvider
    status: CampaignDeploymentStatus
    external_campaign_id: str | None
    submitted_at: datetime | None
    confirmed_at: datetime | None
    last_error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, deployment: CampaignDeployment) -> "CampaignDeploymentRead":
        """Build a CampaignDeploymentRead from a CampaignDeployment ORM instance."""
        return cls(
            id=deployment.id,
            campaign_id=deployment.campaign_id,
            provider=deployment.provider,
            status=deployment.status,
            external_campaign_id=deployment.external_campaign_id,
            submitted_at=deployment.submitted_at,
            confirmed_at=deployment.confirmed_at,
            last_error_message=deployment.last_error_message,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
        )


class CampaignDeploymentListResponse(BaseModel):
    """Response body for POST /campaigns/{campaign_id}/publish and
    GET /campaigns/{campaign_id}/deployments."""

    model_config = ConfigDict(extra="forbid")

    items: list[CampaignDeploymentRead] = Field(default_factory=list)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CampaignRead,
    summary="Create a draft campaign",
    description="Create a new draft campaign for the caller's own tenant. "
    "Only `name` is required; a draft may be otherwise incomplete.",
)
def create_campaign(
    payload: CampaignCreateRequest,
    current_user: User = Depends(get_current_user),
    campaign_service: CampaignService = Depends(get_campaign_service),
) -> CampaignRead:
    """Create a new draft campaign owned by the authenticated user's tenant."""
    campaign = campaign_service.create_draft(
        tenant_id=current_user.tenant_id,
        created_by_user_id=current_user.id,
        name=payload.name,
        objective=payload.objective,
        budget_type=payload.budget_type,
        budget_amount=payload.budget_amount,
        currency=payload.currency,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )
    return CampaignRead.from_model(campaign)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=CampaignListResponse,
    summary="List campaigns for the current tenant",
    description="Return a paginated list of the caller's tenant's "
    "campaigns. Archived campaigns are included by default; pass "
    "`status` to filter to exactly one lifecycle state.",
)
def list_campaigns(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: CampaignStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    campaign_service: CampaignService = Depends(get_campaign_service),
) -> CampaignListResponse:
    """Return a paginated, optionally status-filtered list of campaigns."""
    campaigns = campaign_service.list_campaigns(
        tenant_id=current_user.tenant_id,
        limit=limit,
        offset=offset,
        status=status_filter,
    )
    return CampaignListResponse(items=[CampaignRead.from_model(c) for c in campaigns])


@router.get(
    "/{campaign_id}",
    status_code=status.HTTP_200_OK,
    response_model=CampaignRead,
    summary="Retrieve a single campaign",
    description="Return a single campaign owned by the caller's tenant. "
    "A campaign belonging to another tenant is indistinguishable from a "
    "missing one (404).",
)
def get_campaign(
    campaign_id: UUID,
    current_user: User = Depends(get_current_user),
    campaign_service: CampaignService = Depends(get_campaign_service),
) -> CampaignRead:
    """Return a single tenant-scoped campaign by ID."""
    campaign = campaign_service.get_campaign(
        tenant_id=current_user.tenant_id, campaign_id=campaign_id
    )
    return CampaignRead.from_model(campaign)


@router.patch(
    "/{campaign_id}",
    status_code=status.HTTP_200_OK,
    response_model=CampaignRead,
    summary="Update a draft campaign",
    description="Partially update a campaign that is still a draft. "
    "Editing a non-draft campaign returns 409.",
)
def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdateRequest,
    current_user: User = Depends(get_current_user),
    campaign_service: CampaignService = Depends(get_campaign_service),
) -> CampaignRead:
    """Apply a partial update to a draft campaign."""
    updates: dict[str, Any] = payload.model_dump(exclude_unset=True)
    campaign = campaign_service.update_draft(
        tenant_id=current_user.tenant_id,
        campaign_id=campaign_id,
        updates=updates,
    )
    return CampaignRead.from_model(campaign)


@router.post(
    "/{campaign_id}/archive",
    status_code=status.HTTP_200_OK,
    response_model=CampaignRead,
    summary="Archive a draft campaign",
    description="Transition a draft campaign to archived, a terminal "
    "business state. Does not soft-delete the row -- archiving a "
    "non-draft campaign returns 409.",
)
def archive_campaign(
    campaign_id: UUID,
    current_user: User = Depends(get_current_user),
    campaign_service: CampaignService = Depends(get_campaign_service),
) -> CampaignRead:
    """Archive a draft campaign."""
    campaign = campaign_service.archive_campaign(
        tenant_id=current_user.tenant_id, campaign_id=campaign_id
    )
    return CampaignRead.from_model(campaign)


@router.post(
    "/{campaign_id}/publish",
    status_code=status.HTTP_200_OK,
    response_model=CampaignDeploymentListResponse,
    summary="Publish a campaign to every supported provider",
    description="Ensure a deployment exists for every supported "
    "provider (Meta, Google) and attempt to publish each one that is "
    "still pending; a deployment already past pending is returned "
    "unchanged rather than re-attempted. Real provider adapters are "
    "not implemented yet, so a newly attempted deployment currently "
    "ends up failed rather than submitted -- see app/adapters/. "
    "Publishing an archived campaign returns 409.",
)
def publish_campaign(
    campaign_id: UUID,
    current_user: User = Depends(get_current_user),
    publish_service: PublishCampaignService = Depends(get_publish_campaign_service),
) -> CampaignDeploymentListResponse:
    """Publish a campaign, returning the resulting deployment records."""
    deployments = publish_service.publish_campaign(
        tenant_id=current_user.tenant_id, campaign_id=campaign_id
    )
    return CampaignDeploymentListResponse(
        items=[CampaignDeploymentRead.from_model(d) for d in deployments]
    )


@router.get(
    "/{campaign_id}/deployments",
    status_code=status.HTTP_200_OK,
    response_model=CampaignDeploymentListResponse,
    summary="List a campaign's provider deployments",
    description="Return every deployment record for one campaign, in "
    "deterministic order. A campaign belonging to another tenant is "
    "indistinguishable from a missing one (404).",
)
def list_campaign_deployments(
    campaign_id: UUID,
    current_user: User = Depends(get_current_user),
    publish_service: PublishCampaignService = Depends(get_publish_campaign_service),
) -> CampaignDeploymentListResponse:
    """Return a tenant-scoped, campaign-scoped list of deployments."""
    deployments = publish_service.list_deployments(
        tenant_id=current_user.tenant_id, campaign_id=campaign_id
    )
    return CampaignDeploymentListResponse(
        items=[CampaignDeploymentRead.from_model(d) for d in deployments]
    )

"""Operational metrics endpoint (JSON counts only; no Prometheus)."""

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_current_user, get_metrics_service
from app.models.user import User
from app.services.metrics_service import MetricsService

router = APIRouter(prefix="/metrics", tags=["metrics"])


class PublishJobMetrics(BaseModel):
    """Publish-job status counts for the caller's tenant."""

    model_config = ConfigDict(extra="forbid")

    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    total: int = Field(ge=0)


class CampaignMetrics(BaseModel):
    """Campaign counts for the caller's tenant."""

    model_config = ConfigDict(extra="forbid")

    active: int = Field(ge=0)
    archived: int = Field(ge=0)
    total: int = Field(ge=0)


class ProviderConnectionMetrics(BaseModel):
    """Provider-connection status counts for the caller's tenant."""

    model_config = ConfigDict(extra="forbid")

    connected: int = Field(ge=0)
    disconnected: int = Field(ge=0)
    total: int = Field(ge=0)


class MetricsResponse(BaseModel):
    """GET /metrics body — operational counts only."""

    model_config = ConfigDict(extra="forbid")

    publish_jobs: PublishJobMetrics
    campaigns: CampaignMetrics
    provider_connections: ProviderConnectionMetrics


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=MetricsResponse,
    summary="Operational metrics",
    description="Tenant-scoped aggregate counts. JSON only; no Prometheus, "
    "no credentials, no derived analytics.",
)
def get_metrics(
    current_user: User = Depends(get_current_user),
    metrics_service: MetricsService = Depends(get_metrics_service),
) -> dict[str, Any]:
    """Return operational counts for the authenticated tenant."""
    return metrics_service.get_metrics(tenant_id=current_user.tenant_id)

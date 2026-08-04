"""Read-only operational count metrics (Operational Metrics Foundation).

Aggregate COUNT queries only. Never commits, rolls back, loads ORM rows
for counting, or touches credentials / tokens / connection strings.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignStatus
from app.models.provider_connection import (
    ProviderConnection,
    ProviderConnectionStatus,
)
from app.models.publish_job import PublishJob, PublishJobStatus


class MetricsService:
    """Tenant-scoped operational counters. Read-only; no writes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_metrics(self, *, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Return operational counts for one tenant.

        Campaigns: ``archived`` is ``CampaignStatus.ARCHIVED``; ``active``
        is every non-archived campaign (including draft and future
        non-archived statuses). Publish jobs and provider connections use
        their status enum values directly.
        """
        return {
            "publish_jobs": self._publish_job_counts(tenant_id),
            "campaigns": self._campaign_counts(tenant_id),
            "provider_connections": self._provider_connection_counts(tenant_id),
        }

    def _counts_by_status(
        self,
        *,
        model: type,
        status_column: Any,
        tenant_id: uuid.UUID,
    ) -> dict[Any, int]:
        stmt = (
            select(status_column, func.count())
            .where(model.tenant_id == tenant_id)
            .group_by(status_column)
        )
        rows = self._session.execute(stmt).all()
        return {status: int(count) for status, count in rows}

    def _publish_job_counts(self, tenant_id: uuid.UUID) -> dict[str, int]:
        by_status = self._counts_by_status(
            model=PublishJob,
            status_column=PublishJob.status,
            tenant_id=tenant_id,
        )
        queued = by_status.get(PublishJobStatus.QUEUED, 0)
        running = by_status.get(PublishJobStatus.RUNNING, 0)
        succeeded = by_status.get(PublishJobStatus.SUCCEEDED, 0)
        failed = by_status.get(PublishJobStatus.FAILED, 0)
        return {
            "queued": queued,
            "running": running,
            "succeeded": succeeded,
            "failed": failed,
            "total": queued + running + succeeded + failed,
        }

    def _campaign_counts(self, tenant_id: uuid.UUID) -> dict[str, int]:
        by_status = self._counts_by_status(
            model=Campaign,
            status_column=Campaign.status,
            tenant_id=tenant_id,
        )
        archived = by_status.get(CampaignStatus.ARCHIVED, 0)
        total = sum(by_status.values())
        active = total - archived
        return {
            "active": active,
            "archived": archived,
            "total": total,
        }

    def _provider_connection_counts(self, tenant_id: uuid.UUID) -> dict[str, int]:
        by_status = self._counts_by_status(
            model=ProviderConnection,
            status_column=ProviderConnection.status,
            tenant_id=tenant_id,
        )
        connected = by_status.get(ProviderConnectionStatus.CONNECTED, 0)
        disconnected = by_status.get(ProviderConnectionStatus.DISCONNECTED, 0)
        return {
            "connected": connected,
            "disconnected": disconnected,
            "total": connected + disconnected,
        }

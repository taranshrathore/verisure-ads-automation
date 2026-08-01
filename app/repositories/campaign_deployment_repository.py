"""CampaignDeployment persistence repository."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaign_deployment import (
    CampaignDeployment,
    CampaignDeploymentProvider,
    CampaignDeploymentStatus,
)

# Sentinel distinguishing "caller did not pass this field" from "caller
# explicitly passed None" in update_status, so an unrelated status change
# never silently clobbers a previously-set timestamp/error field.
_UNSET: Any = object()


class CampaignDeploymentRepository:
    """Data-access helpers for CampaignDeployment rows. Does not commit or roll back.

    Every method is tenant-scoped: there is no method here that can look
    up or mutate a deployment without a tenant_id predicate.

    Unlike Campaign, CampaignDeployment has no deleted_at column (no
    SoftDeleteMixin) -- reads here are therefore not soft-delete filtered.
    TODO: if deployments need soft-deletion later, that requires adding
    SoftDeleteMixin plus an additive migration; not needed by anything
    today, so deliberately not implemented here.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, deployment: CampaignDeployment) -> None:
        """Stage a new deployment for persistence."""
        self._session.add(deployment)

    def get_by_id(
        self, tenant_id: UUID, deployment_id: UUID
    ) -> CampaignDeployment | None:
        """Return a tenant-scoped deployment by ID, or None."""
        return self._session.scalar(
            select(CampaignDeployment).where(
                CampaignDeployment.id == deployment_id,
                CampaignDeployment.tenant_id == tenant_id,
            )
        )

    def get_by_campaign_and_provider(
        self,
        tenant_id: UUID,
        campaign_id: UUID,
        provider: CampaignDeploymentProvider,
    ) -> CampaignDeployment | None:
        """Return the tenant-scoped deployment for one campaign+provider pair, or None."""
        return self._session.scalar(
            select(CampaignDeployment).where(
                CampaignDeployment.tenant_id == tenant_id,
                CampaignDeployment.campaign_id == campaign_id,
                CampaignDeployment.provider == provider,
            )
        )

    def list_by_campaign(
        self, tenant_id: UUID, campaign_id: UUID
    ) -> list[CampaignDeployment]:
        """Return every tenant-scoped deployment for one campaign.

        Not paginated: uq_campaign_deployments_campaign_id_provider bounds
        this to at most one row per CampaignDeploymentProvider member.
        Ordered by created_at/id for a deterministic result.
        """
        stmt = (
            select(CampaignDeployment)
            .where(
                CampaignDeployment.tenant_id == tenant_id,
                CampaignDeployment.campaign_id == campaign_id,
            )
            .order_by(
                CampaignDeployment.created_at.asc(), CampaignDeployment.id.asc()
            )
        )
        return list(self._session.scalars(stmt))

    def get_by_idempotency_key(
        self, tenant_id: UUID, idempotency_key: str
    ) -> CampaignDeployment | None:
        """Return the tenant-scoped deployment for one idempotency key, or None."""
        return self._session.scalar(
            select(CampaignDeployment).where(
                CampaignDeployment.tenant_id == tenant_id,
                CampaignDeployment.idempotency_key == idempotency_key,
            )
        )

    def update_status(
        self,
        tenant_id: UUID,
        deployment_id: UUID,
        expected_current_status: CampaignDeploymentStatus,
        new_status: CampaignDeploymentStatus,
        *,
        submitted_at: datetime | None = _UNSET,
        confirmed_at: datetime | None = _UNSET,
        last_error_message: str | None = _UNSET,
    ) -> int:
        """Optimistically-conditional update of a deployment's status.

        The WHERE clause re-checks status == expected_current_status at
        write time, not merely at the caller's earlier read time -- the
        same conditional-UPDATE-with-rowcount pattern
        CampaignRepository.archive_draft/update_draft_fields use to close
        the race where a concurrent transition lands between a read and
        this write. Returns the affected row count so the caller can
        distinguish a lost race or tenant/ID mismatch (0 rows) from
        success (1 row) without a second query. Which transitions between
        which statuses are legal is still a service-only concern -- this
        method only re-verifies the *specific* expected_current_status
        the caller already decided to transition from.

        submitted_at/confirmed_at/last_error_message are only included in
        the SET clause when explicitly passed -- their default is a
        sentinel, not None -- so a status-only change never overwrites a
        field the caller didn't mention. Passing None explicitly still
        clears that field.
        """
        values: dict[str, object] = {"status": new_status}
        if submitted_at is not _UNSET:
            values["submitted_at"] = submitted_at
        if confirmed_at is not _UNSET:
            values["confirmed_at"] = confirmed_at
        if last_error_message is not _UNSET:
            values["last_error_message"] = last_error_message

        result = self._session.execute(
            update(CampaignDeployment)
            .where(
                CampaignDeployment.id == deployment_id,
                CampaignDeployment.tenant_id == tenant_id,
                CampaignDeployment.status == expected_current_status,
            )
            .values(**values)
        )
        return result.rowcount

    def update_external_reference(
        self, tenant_id: UUID, deployment_id: UUID, external_campaign_id: str
    ) -> int:
        """Conditionally set the provider-assigned external_campaign_id.

        Returns the affected row count so the caller can detect a
        tenant/ID mismatch (0 rows) versus success (1 row).
        """
        result = self._session.execute(
            update(CampaignDeployment)
            .where(
                CampaignDeployment.id == deployment_id,
                CampaignDeployment.tenant_id == tenant_id,
            )
            .values(external_campaign_id=external_campaign_id)
        )
        return result.rowcount

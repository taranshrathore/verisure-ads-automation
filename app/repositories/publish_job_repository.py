"""PublishJob persistence repository.

ASYNC PUBLISH PHASE 2 SCOPE: data-access helpers only. Does not commit
or roll back. PublishJobService (Phase 3) owns transactions on top of
these primitives. No worker entrypoint or HTTP API wiring lives here.
"""

from datetime import datetime
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.publish_job import PublishJob, PublishJobStatus

# Sentinel distinguishing "caller did not pass error_message" from
# "caller explicitly passed None" in mark_finished.
_UNSET: Any = object()


class ClaimResult(NamedTuple):
    """Outcome of claim_next: the claimed row and whether it was a reclaim."""

    job: PublishJob
    reclaimed: bool


class PublishJobRepository:
    """Data-access helpers for PublishJob rows. Does not commit or roll back.

    Every read/mutate method that is tenant-scoped takes tenant_id
    explicitly (create/claim_next are the exceptions: create stages a
    caller-built row; claim_next is a global worker claim across tenants).
    Missing and cross-tenant lookups are indistinguishable (None / zero
    rows), matching the rest of this codebase.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, job: PublishJob) -> None:
        """Stage a new publish job for persistence."""
        self._session.add(job)

    def get_by_id(self, tenant_id: UUID, job_id: UUID) -> PublishJob | None:
        """Return a tenant-scoped publish job by ID, or None."""
        return self._session.scalar(
            select(PublishJob).where(
                PublishJob.id == job_id,
                PublishJob.tenant_id == tenant_id,
            )
        )

    def get_active_for_campaign(
        self, tenant_id: UUID, campaign_id: UUID
    ) -> PublishJob | None:
        """Return the queued or running job for one campaign, if any.

        Terminal statuses (succeeded/failed) are excluded. Ordered by
        created_at/id for determinism; the partial unique active index
        already guarantees at most one matching row.
        """
        return self._session.scalar(
            select(PublishJob)
            .where(
                PublishJob.tenant_id == tenant_id,
                PublishJob.campaign_id == campaign_id,
                PublishJob.status.in_(
                    (PublishJobStatus.QUEUED, PublishJobStatus.RUNNING)
                ),
            )
            .order_by(PublishJob.created_at.asc(), PublishJob.id.asc())
            .limit(1)
        )

    def claim_next(
        self,
        started_at: datetime,
        *,
        stale_before: datetime | None = None,
    ) -> ClaimResult | None:
        """Atomically claim the next work item for a worker.

        Preference order:
        1. Oldest QUEUED job (status -> RUNNING, lease started_at,
           attempt_count += 1).
        2. If none and stale_before is set: oldest RUNNING job whose
           started_at is older than stale_before (lease refresh only —
           status stays RUNNING, attempt_count += 1).

        Candidate selection uses FOR UPDATE SKIP LOCKED so concurrent
        workers never claim the same row. Does not commit or roll back.
        """
        queued = self._claim_queued(started_at)
        if queued is not None:
            return ClaimResult(job=queued, reclaimed=False)
        if stale_before is None:
            return None
        reclaimed = self._reclaim_stale(started_at, stale_before)
        if reclaimed is None:
            return None
        return ClaimResult(job=reclaimed, reclaimed=True)

    def _claim_queued(self, started_at: datetime) -> PublishJob | None:
        # updated_at must be set explicitly: TimestampMixin.onupdate is an
        # ORM-level hook and does not fire for Core UPDATE statements.
        candidate = (
            select(PublishJob.id)
            .where(PublishJob.status == PublishJobStatus.QUEUED)
            .order_by(PublishJob.created_at.asc(), PublishJob.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        result = self._session.execute(
            update(PublishJob)
            .where(PublishJob.id == candidate)
            .values(
                status=PublishJobStatus.RUNNING,
                started_at=started_at,
                attempt_count=PublishJob.attempt_count + 1,
                updated_at=started_at,
            )
            .returning(PublishJob)
        )
        return result.scalar_one_or_none()

    def _reclaim_stale(
        self, started_at: datetime, stale_before: datetime
    ) -> PublishJob | None:
        candidate = (
            select(PublishJob.id)
            .where(
                PublishJob.status == PublishJobStatus.RUNNING,
                PublishJob.started_at.is_not(None),
                PublishJob.started_at < stale_before,
            )
            .order_by(PublishJob.started_at.asc(), PublishJob.id.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
            .scalar_subquery()
        )
        result = self._session.execute(
            update(PublishJob)
            .where(PublishJob.id == candidate)
            .values(
                started_at=started_at,
                attempt_count=PublishJob.attempt_count + 1,
                updated_at=started_at,
            )
            .returning(PublishJob)
        )
        return result.scalar_one_or_none()

    def mark_finished(
        self,
        tenant_id: UUID,
        job_id: UUID,
        expected_current_status: PublishJobStatus,
        new_status: PublishJobStatus,
        finished_at: datetime,
        *,
        error_message: str | None = _UNSET,
    ) -> int:
        """Optimistically-conditional finish of a publish job.

        WHERE re-checks tenant_id, id, and status == expected_current_status
        at write time -- the same conditional-UPDATE-with-rowcount
        pattern CampaignDeploymentRepository.update_status and
        ProviderConnectionRepository.disconnect use. Returns affected row
        count (1 on success, 0 on lost race / mismatch / cross-tenant).

        error_message is only included in the SET clause when explicitly
        passed -- default is a sentinel, not None -- so a finish that
        omits it never clobbers a previously-set message. Passing None
        explicitly still clears the field.
        """
        values: dict[str, object] = {
            "status": new_status,
            "finished_at": finished_at,
            "updated_at": finished_at,
        }
        if error_message is not _UNSET:
            values["error_message"] = error_message

        result = self._session.execute(
            update(PublishJob)
            .where(
                PublishJob.id == job_id,
                PublishJob.tenant_id == tenant_id,
                PublishJob.status == expected_current_status,
            )
            .values(**values)
        )
        return result.rowcount

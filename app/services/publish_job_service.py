"""PublishJob lifecycle service: enqueue, read, and worker run_once.

ASYNC PUBLISH PHASE 3 SCOPE: service-layer orchestration only. No HTTP
API, no worker entrypoint/polling loop. PublishJobService owns all
transaction commits for run_once (including deployment writes via
publish_campaign(commit=False)); PublishJobRepository never commits.
The worker boundary later must call only this service (then
PublishCampaignService), never routers or FastAPI Request objects.

Observability: lifecycle logs only (enqueue/claim/succeed/fail). Business
failures are logged once here; the worker must not re-log them as
unexpected_worker_error (see consume_business_failure_logged).
"""

import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignNotFoundError,
    InvalidCampaignStateError,
    PublishJobNotFoundError,
)
from app.core.logging_context import bind, bound_context, reset
from app.models.campaign import CampaignStatus
from app.models.publish_job import PublishJob, PublishJobStatus
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.publish_job_repository import PublishJobRepository
from app.services.publish_campaign_service import PublishCampaignService

# Matches the partial unique index created by migration b7e4a91c2d08.
_UQ_ACTIVE_CAMPAIGN = "uq_publish_jobs_campaign_id_active"

# Must not exceed PublishJob.error_message's column width (String(2000)).
_MAX_ERROR_MESSAGE_LENGTH = 2000
_TRUNCATION_SUFFIX = "...[truncated]"
_FALLBACK_ERROR_MESSAGE = "Publish job failed without a usable error message."

logger = logging.getLogger("verisure.publish_job")

# Set when publish_job_failed has already been emitted for the current
# run_once failure path so the worker does not also emit
# unexpected_worker_error for the same business failure.
_business_failure_logged: ContextVar[bool] = ContextVar(
    "publish_job_business_failure_logged", default=False
)


def consume_business_failure_logged() -> bool:
    """Return True once if PublishJobService already logged a job failure."""
    if _business_failure_logged.get():
        _business_failure_logged.set(False)
        return True
    return False


def _safe_job_error_message(
    raw_message: str | None, *, exception_type: str | None = None
) -> str:
    """Turn an arbitrary exception string into a bounded, non-empty message."""
    message = (raw_message or "").strip()
    if not message:
        message = _FALLBACK_ERROR_MESSAGE
        if exception_type:
            message = f"{message} ({exception_type})"

    if len(message) <= _MAX_ERROR_MESSAGE_LENGTH:
        return message

    keep = _MAX_ERROR_MESSAGE_LENGTH - len(_TRUNCATION_SUFFIX)
    return message[:keep] + _TRUNCATION_SUFFIX


def _log_enqueued(job: PublishJob) -> None:
    with bound_context(
        job_id=str(job.id),
        tenant_id=str(job.tenant_id),
        campaign_id=str(job.campaign_id),
        status=job.status.value,
        service="api",
    ):
        logger.info("publish_job_enqueued")


class PublishJobService:
    """Orchestrates publish-job enqueue, lookup, and one-shot execution."""

    def __init__(
        self,
        repository: PublishJobRepository,
        campaign_repository: CampaignRepository,
        publish_campaign_service: PublishCampaignService,
        session: Session,
    ) -> None:
        self._jobs = repository
        self._campaigns = campaign_repository
        self._publish = publish_campaign_service
        self._session = session

    def enqueue(
        self,
        *,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        requested_by_user_id: uuid.UUID | None,
    ) -> PublishJob:
        """Enqueue a queued publish job for one campaign, or return the active one.

        Missing/cross-tenant campaigns raise CampaignNotFoundError. Archived
        campaigns raise InvalidCampaignStateError. An existing queued or
        running job for the same campaign is returned as-is (idempotent
        enqueue). Concurrent inserts racing the partial unique index are
        resolved by re-reading the active job after rollback.
        """
        try:
            campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
            if campaign is None:
                raise CampaignNotFoundError()
            if campaign.status == CampaignStatus.ARCHIVED:
                raise InvalidCampaignStateError(
                    "An archived campaign cannot be published."
                )

            active = self._jobs.get_active_for_campaign(tenant_id, campaign_id)
            if active is not None:
                if (
                    active.tenant_id != tenant_id
                    or active.campaign_id != campaign_id
                ):
                    raise RuntimeError(
                        "Active publish job tenant/campaign mismatch "
                        "from repository."
                    )
                _log_enqueued(active)
                return active

            job = PublishJob(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                requested_by_user_id=requested_by_user_id,
                status=PublishJobStatus.QUEUED,
            )
            self._jobs.create(job)
            self._session.commit()
            _log_enqueued(job)
            return job
        except IntegrityError as exc:
            self._session.rollback()
            if _UQ_ACTIVE_CAMPAIGN in str(getattr(exc, "orig", exc)):
                existing = self._jobs.get_active_for_campaign(
                    tenant_id, campaign_id
                )
                if existing is not None:
                    if (
                        existing.tenant_id != tenant_id
                        or existing.campaign_id != campaign_id
                    ):
                        raise RuntimeError(
                            "Active publish job tenant/campaign mismatch "
                            "from repository."
                        ) from exc
                    _log_enqueued(existing)
                    return existing
            raise
        except Exception:
            self._session.rollback()
            raise

    def get_job(
        self,
        *,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> PublishJob:
        """Return one job scoped to both tenant and campaign, or raise."""
        job = self._jobs.get_by_id(tenant_id, job_id)
        if job is None or job.campaign_id != campaign_id:
            raise PublishJobNotFoundError()
        return job

    def run_once(self) -> bool:
        """Claim one queued job, run publish, and mark the job terminal.

        Single unit of work for the worker path: claim, publish
        (deployments flush-only), and terminal job mark share one outer
        transaction. A savepoint wraps publish + success mark so a
        failure rolls back deployment work while leaving the claim in
        the outer transaction long enough to mark the job FAILED, then
        one commit (or a full rollback if that also fails).

        Returns False when the queue is empty. Returns True after a
        successful publish + SUCCEEDED transition. When publish (or the
        success mark) fails, marks the job FAILED (when still RUNNING),
        commits that failure when possible, and re-raises -- never
        swallowing the original exception.
        """
        now = datetime.now(timezone.utc)
        try:
            job = self._jobs.claim_next(now)
        except Exception:
            self._session.rollback()
            raise

        if job is None:
            return False

        started = time.perf_counter()
        token = bind(
            job_id=str(job.id),
            tenant_id=str(job.tenant_id),
            campaign_id=str(job.campaign_id),
            attempt_count=job.attempt_count,
            service="worker",
        )
        try:
            logger.info("publish_job_claimed")
            try:
                with self._session.begin_nested():
                    self._publish.publish_campaign(
                        tenant_id=job.tenant_id,
                        campaign_id=job.campaign_id,
                        commit=False,
                    )
                    finished_at = datetime.now(timezone.utc)
                    affected = self._jobs.mark_finished(
                        job.tenant_id,
                        job.id,
                        PublishJobStatus.RUNNING,
                        PublishJobStatus.SUCCEEDED,
                        finished_at,
                    )
                    if affected != 1:
                        raise RuntimeError(
                            "Publish job could not be marked succeeded."
                        )
                self._session.commit()
                duration_ms = int((time.perf_counter() - started) * 1000)
                with bound_context(duration_ms=duration_ms):
                    logger.info("publish_job_succeeded")
                return True
            except Exception as exc:
                message = _safe_job_error_message(
                    str(exc), exception_type=type(exc).__name__
                )
                with bound_context(
                    error_type=type(exc).__name__,
                    error_message=message,
                ):
                    logger.info("publish_job_failed")
                _business_failure_logged.set(True)
                try:
                    finished_at = datetime.now(timezone.utc)
                    affected = self._jobs.mark_finished(
                        job.tenant_id,
                        job.id,
                        PublishJobStatus.RUNNING,
                        PublishJobStatus.FAILED,
                        finished_at,
                        error_message=message,
                    )
                    if affected != 1:
                        self._session.rollback()
                    else:
                        self._session.commit()
                except Exception:
                    self._session.rollback()
                    raise
                raise
        finally:
            reset(token)

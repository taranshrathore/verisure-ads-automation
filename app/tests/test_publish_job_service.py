"""Integration tests for PublishJobService (Async Publish Phase 3).

No API routes, no worker entrypoint. Fake PublishCampaignService is used
so these tests isolate job-lifecycle orchestration from adapter publish.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignNotFoundError,
    InvalidCampaignStateError,
    PublishJobNotFoundError,
)
from app.core.provider_error_sanitization import (
    PROVIDER_REQUEST_FAILED,
    UNEXPECTED_PROVIDER_ERROR,
    sanitize_provider_exception,
    sanitize_provider_message,
)
from app.models.campaign import Campaign, CampaignStatus
from app.models.publish_job import PublishJob, PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.publish_job_repository import PublishJobRepository
from app.services.publish_job_service import PublishJobService


class FakePublishCampaignService:
    """Minimal stand-in: records calls and optionally raises."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple] = []

    def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
        self.calls.append((tenant_id, campaign_id, commit))
        if self.error is not None:
            raise self.error
        return []


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Publish Job Service Tenant {suffix}",
        slug=f"publish-job-svc-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"publish-job-svc-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Publish job service campaign {suffix}",
    )
    db_session.add(campaign)
    db_session.flush()
    return tenant, user, campaign


def _make_campaign(
    db_session: Session, tenant: Tenant, user: User, *, name: str
) -> Campaign:
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=name,
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _make_service(
    db_session: Session,
    *,
    publish: FakePublishCampaignService | None = None,
) -> tuple[PublishJobService, PublishJobRepository, FakePublishCampaignService]:
    job_repo = PublishJobRepository(db_session)
    campaign_repo = CampaignRepository(db_session)
    fake_publish = publish or FakePublishCampaignService()
    service = PublishJobService(
        job_repo, campaign_repo, fake_publish, db_session  # type: ignore[arg-type]
    )
    return service, job_repo, fake_publish


# --- enqueue -----------------------------------------------------------------


def test_enqueue_happy_path(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-ok")
    service, _, _ = _make_service(db_session)

    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    assert job.id is not None
    assert job.tenant_id == tenant.id
    assert job.campaign_id == campaign.id
    assert job.requested_by_user_id == user.id
    assert job.status == PublishJobStatus.QUEUED
    assert job.attempt_count == 0


def test_enqueue_archived_campaign(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-arch")
    campaign.status = CampaignStatus.ARCHIVED
    db_session.flush()
    service, _, _ = _make_service(db_session)

    with pytest.raises(InvalidCampaignStateError, match="archived"):
        service.enqueue(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
        )


def test_enqueue_campaign_missing(db_session: Session) -> None:
    tenant, user, _ = _make_tenant_user_campaign(db_session, suffix="enq-miss")
    service, _, _ = _make_service(db_session)

    with pytest.raises(CampaignNotFoundError):
        service.enqueue(
            tenant_id=tenant.id,
            campaign_id=uuid4(),
            requested_by_user_id=user.id,
        )


def test_enqueue_active_queued_returns_same_job(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-q")
    service, _, _ = _make_service(db_session)

    first = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    second = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    assert second.id == first.id
    assert second.status == PublishJobStatus.QUEUED


def test_enqueue_active_running_returns_same_job(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-r")
    service, job_repo, _ = _make_service(db_session)

    queued = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    claimed = job_repo.claim_next(datetime.now(timezone.utc))
    assert claimed is not None
    assert claimed.job.id == queued.id
    assert claimed.reclaimed is False
    db_session.commit()

    again = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert again.id == queued.id
    assert again.status == PublishJobStatus.RUNNING


def test_enqueue_unique_race_returns_existing(db_session: Session) -> None:
    """TOCTOU: get_active returns None but a concurrent row already exists."""
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-race")
    db_session.commit()
    service, job_repo, _ = _make_service(db_session)

    existing = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    calls = {"n": 0}
    original = job_repo.get_active_for_campaign

    def flaky(tenant_id, campaign_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original(tenant_id, campaign_id)

    job_repo.get_active_for_campaign = flaky  # type: ignore[method-assign]
    try:
        raced = service.enqueue(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
        )
    finally:
        job_repo.get_active_for_campaign = original  # type: ignore[method-assign]

    assert raced.id == existing.id
    assert calls["n"] >= 2


def test_enqueue_after_terminal_creates_new_job(db_session: Session) -> None:
    """Succeeded/failed jobs are not active -- a later enqueue is a new row."""
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq-term")
    service, _, _ = _make_service(db_session)

    first = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert service.run_once() is True

    second = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert second.id != first.id
    assert second.status == PublishJobStatus.QUEUED


def test_enqueue_cross_tenant_campaign_raises_not_found(db_session: Session) -> None:
    tenant_a, user_a, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="enq-xta"
    )
    tenant_b, user_b, _ = _make_tenant_user_campaign(db_session, suffix="enq-xtb")
    service, _, _ = _make_service(db_session)

    with pytest.raises(CampaignNotFoundError):
        service.enqueue(
            tenant_id=tenant_b.id,
            campaign_id=campaign_a.id,
            requested_by_user_id=user_b.id,
        )


# --- get_job -----------------------------------------------------------------


def test_get_job_happy(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="get-ok")
    service, _, _ = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    loaded = service.get_job(
        tenant_id=tenant.id, campaign_id=campaign.id, job_id=job.id
    )
    assert loaded.id == job.id


def test_get_job_wrong_tenant_404(db_session: Session) -> None:
    tenant_a, user_a, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="get-ta"
    )
    tenant_b, _, _ = _make_tenant_user_campaign(db_session, suffix="get-tb")
    service, _, _ = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant_a.id,
        campaign_id=campaign_a.id,
        requested_by_user_id=user_a.id,
    )

    with pytest.raises(PublishJobNotFoundError):
        service.get_job(
            tenant_id=tenant_b.id, campaign_id=campaign_a.id, job_id=job.id
        )


def test_get_job_wrong_campaign_404(db_session: Session) -> None:
    tenant, user, campaign_a = _make_tenant_user_campaign(db_session, suffix="get-ca")
    campaign_b = _make_campaign(
        db_session, tenant, user, name="other campaign for get_job"
    )
    service, _, _ = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign_a.id,
        requested_by_user_id=user.id,
    )

    with pytest.raises(PublishJobNotFoundError):
        service.get_job(
            tenant_id=tenant.id, campaign_id=campaign_b.id, job_id=job.id
        )


# --- run_once ----------------------------------------------------------------


def test_run_once_empty_queue_returns_false(db_session: Session) -> None:
    service, _, fake = _make_service(db_session)

    assert service.run_once() is False
    assert fake.calls == []


def test_run_once_success_marks_succeeded(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="run-ok")
    service, job_repo, fake = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    assert service.run_once() is True
    assert fake.calls == [(tenant.id, campaign.id, False)]

    loaded = job_repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.SUCCEEDED
    assert loaded.finished_at is not None
    assert loaded.attempt_count == 1
    assert loaded.error_message is None


def test_run_once_publish_exception_marks_failed(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="run-fail")
    fake = FakePublishCampaignService(error=RuntimeError("adapter exploded"))
    service, job_repo, _ = _make_service(db_session, publish=fake)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    with pytest.raises(RuntimeError, match="adapter exploded"):
        service.run_once()

    loaded = job_repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.FAILED
    assert loaded.finished_at is not None
    assert loaded.attempt_count == 1
    assert loaded.error_message is not None
    assert loaded.error_message == PROVIDER_REQUEST_FAILED
    assert "adapter exploded" not in loaded.error_message


def test_unexpected_persistence_failure_rolls_back(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="run-persist")
    fake = FakePublishCampaignService(error=RuntimeError("publish boom"))
    service, job_repo, _ = _make_service(db_session, publish=fake)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    original = job_repo.mark_finished

    def boom(*args, **kwargs):
        raise RuntimeError("persist boom")

    job_repo.mark_finished = boom  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="persist boom"):
            service.run_once()
    finally:
        job_repo.mark_finished = original  # type: ignore[method-assign]

    loaded = job_repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    # Claim + failed mark were rolled back; job remains queued.
    assert loaded.status == PublishJobStatus.QUEUED
    assert loaded.attempt_count == 0


def test_terminal_mark_failure_after_flush_only_publish_rolls_back_claim(
    db_session: Session,
) -> None:
    """Without nested commits, a failed terminal mark rolls back the claim.

    Publish no longer commits the claim mid-flight. If both SUCCEEDED and
    FAILED terminal marks raise, the outer transaction rolls back and the
    job remains QUEUED (not stuck RUNNING).
    """

    class _FlushOnlyPublish:
        def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
            del tenant_id, campaign_id, commit
            return []

    tenant, user, campaign = _make_tenant_user_campaign(
        db_session, suffix="run-ok-persist"
    )
    service, job_repo, _ = _make_service(
        db_session, publish=_FlushOnlyPublish()  # type: ignore[arg-type]
    )
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    original = job_repo.mark_finished

    def boom(*args, **kwargs):
        raise RuntimeError("terminal mark boom")

    job_repo.mark_finished = boom  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="terminal mark boom"):
            service.run_once()
    finally:
        job_repo.mark_finished = original  # type: ignore[method-assign]

    loaded = job_repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.QUEUED
    assert loaded.attempt_count == 0


def test_repeated_run_once_does_not_reclaim_terminal_job(
    db_session: Session,
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="run-repeat")
    service, job_repo, fake = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    assert service.run_once() is True
    assert service.run_once() is False
    assert len(fake.calls) == 1

    loaded = job_repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.status == PublishJobStatus.SUCCEEDED


def test_get_job_sees_committed_terminal_status_after_run_once(
    db_session: Session,
) -> None:
    """expire_on_commit must not leave get_job reading a stale RUNNING row."""
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="run-idmap")
    service, _, _ = _make_service(db_session)
    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    assert service.run_once() is True
    loaded = service.get_job(
        tenant_id=tenant.id, campaign_id=campaign.id, job_id=job.id
    )
    assert loaded.status == PublishJobStatus.SUCCEEDED
    assert loaded.finished_at is not None


# --- error-message sanitization ----------------------------------------------


def test_provider_error_sanitization_never_returns_raw_text() -> None:
    huge = "x" * 5000
    message = sanitize_provider_message(huge)
    assert message == PROVIDER_REQUEST_FAILED
    assert huge not in message

    message = sanitize_provider_exception(RuntimeError(""))
    assert message == UNEXPECTED_PROVIDER_ERROR
    assert "RuntimeError" not in message


# --- session / transaction ownership -----------------------------------------


def test_session_usable_after_rollback(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="sess")
    db_session.commit()
    service, _, _ = _make_service(db_session)

    with pytest.raises(CampaignNotFoundError):
        service.enqueue(
            tenant_id=tenant.id,
            campaign_id=uuid4(),
            requested_by_user_id=user.id,
        )

    job = service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    assert job.status == PublishJobStatus.QUEUED


def test_repository_never_commits(db_session: Session) -> None:
    """Calling repository methods directly then rolling back discards rows."""
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="no-c")
    repo = PublishJobRepository(db_session)
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
    )
    repo.create(job)
    db_session.flush()
    job_id = job.id

    claimed = repo.claim_next(datetime.now(timezone.utc))
    assert claimed is not None
    db_session.flush()

    db_session.rollback()

    assert db_session.get(PublishJob, job_id) is None

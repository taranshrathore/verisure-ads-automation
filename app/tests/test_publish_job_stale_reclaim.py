"""Stale RUNNING publish-job reclaim tests.

Lease recovery via claim_next: QUEUED preferred, then oldest stale RUNNING.
No sleeps. Repository never commits; PublishJobService owns the UoW.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.logging import JsonLogFormatter
from app.models.campaign import (
    Campaign,
    CampaignBudgetType,
    CampaignObjective,
)
from app.models.publish_job import PublishJob, PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.publish_job_repository import PublishJobRepository
from app.services.publish_job_service import PublishJobService
from app.tests.database import get_test_engine


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Stale Reclaim Tenant {suffix}",
        slug=f"stale-reclaim-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"stale-reclaim-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Stale reclaim campaign {suffix}",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
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
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("50.00"),
        currency="USD",
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _make_job(
    db_session: Session,
    tenant: Tenant,
    campaign: Campaign,
    *,
    status: PublishJobStatus = PublishJobStatus.QUEUED,
    requested_by_user_id=None,
    started_at: datetime | None = None,
    attempt_count: int = 0,
    created_at: datetime | None = None,
) -> PublishJob:
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=requested_by_user_id,
        status=status,
        started_at=started_at,
        attempt_count=attempt_count,
    )
    if created_at is not None:
        job.created_at = created_at
        job.updated_at = created_at
    db_session.add(job)
    db_session.flush()
    return job


@pytest.fixture
def repo(db_session: Session) -> PublishJobRepository:
    return PublishJobRepository(db_session)


class _FakePublish:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
        self.calls.append((tenant_id, campaign_id, commit))
        return []


class _TrackingSession:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1
        self._session.commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        self._session.rollback()

    def begin_nested(self) -> Any:
        return self._session.begin_nested()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


class _JsonCaptureHandler(logging.Handler):
    """Format immediately so contextvars fields are present on the payload."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonLogFormatter())
        self.payloads: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


# --- repository reclaim ------------------------------------------------------


def test_stale_running_is_reclaimed(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="stale-ok")
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=started,
        attempt_count=1,
    )
    now = started + timedelta(seconds=1000)
    stale_before = now - timedelta(seconds=900)

    claimed = repo.claim_next(now, stale_before=stale_before)
    db_session.refresh(job)

    assert claimed is not None
    assert claimed.reclaimed is True
    assert claimed.job.id == job.id
    assert job.status == PublishJobStatus.RUNNING
    assert job.attempt_count == 2
    assert job.started_at == now


def test_fresh_running_is_ignored(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="fresh")
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    started = now - timedelta(seconds=60)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=started,
        attempt_count=1,
    )
    stale_before = now - timedelta(seconds=900)

    claimed = repo.claim_next(now, stale_before=stale_before)
    db_session.refresh(job)

    assert claimed is None
    assert job.attempt_count == 1
    assert job.started_at == started


def test_queued_preferred_over_stale_running(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign_q = _make_tenant_user_campaign(
        db_session, suffix="pref-q"
    )
    campaign_r = _make_campaign(db_session, tenant, user, name="pref-r")
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=timezone.utc)
    stale = _make_job(
        db_session,
        tenant,
        campaign_r,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now - timedelta(hours=2),
        attempt_count=3,
        created_at=now - timedelta(hours=3),
    )
    queued = _make_job(
        db_session,
        tenant,
        campaign_q,
        status=PublishJobStatus.QUEUED,
        requested_by_user_id=user.id,
        created_at=now - timedelta(minutes=1),
    )
    stale_before = now - timedelta(seconds=900)

    claimed = repo.claim_next(now, stale_before=stale_before)
    db_session.refresh(stale)
    db_session.refresh(queued)

    assert claimed is not None
    assert claimed.reclaimed is False
    assert claimed.job.id == queued.id
    assert queued.status == PublishJobStatus.RUNNING
    assert stale.status == PublishJobStatus.RUNNING
    assert stale.attempt_count == 3
    assert stale.started_at == now - timedelta(hours=2)


def test_reclaim_increments_attempt_count_and_updates_started_at(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="inc")
    old_started = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=old_started,
        attempt_count=4,
    )
    now = old_started + timedelta(hours=1)
    stale_before = now - timedelta(seconds=900)

    claimed = repo.claim_next(now, stale_before=stale_before)

    assert claimed is not None
    assert claimed.reclaimed is True
    assert claimed.job.attempt_count == 5
    assert claimed.job.started_at == now
    assert job.attempt_count == 5
    assert job.started_at == now


def test_reclaim_picks_oldest_stale_by_started_at(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="oldest"
    )
    campaign_b = _make_campaign(db_session, tenant, user, name="oldest-b")
    now = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    _make_job(
        db_session,
        tenant,
        campaign_a,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now - timedelta(hours=1),
        attempt_count=1,
    )
    older_stale = _make_job(
        db_session,
        tenant,
        campaign_b,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now - timedelta(hours=3),
        attempt_count=2,
    )
    stale_before = now - timedelta(seconds=900)

    claimed = repo.claim_next(now, stale_before=stale_before)

    assert claimed is not None
    assert claimed.job.id == older_stale.id


def test_concurrent_workers_reclaim_stale_job_once() -> None:
    """Two open transactions reclaim; SKIP LOCKED ensures one winner."""
    engine = get_test_engine()
    suffix = uuid4().hex[:10]
    started = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)
    now = started + timedelta(hours=2)
    stale_before = now - timedelta(seconds=900)

    setup = Session(bind=engine)
    try:
        tenant = Tenant(
            name=f"Concurrent Reclaim {suffix}",
            slug=f"concurrent-reclaim-{suffix}",
        )
        setup.add(tenant)
        setup.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"concurrent-reclaim-{suffix}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
        )
        setup.add(user)
        setup.flush()
        campaign = Campaign(
            tenant_id=tenant.id,
            created_by_user_id=user.id,
            name=f"concurrent-reclaim-{suffix}",
        )
        setup.add(campaign)
        setup.flush()
        job = PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.RUNNING,
            started_at=started,
            attempt_count=1,
        )
        setup.add(job)
        setup.commit()
        tenant_id = tenant.id
        job_id = job.id
    finally:
        setup.close()

    conn1 = engine.connect()
    conn2 = engine.connect()
    trans1 = conn1.begin()
    trans2 = conn2.begin()
    session1 = Session(bind=conn1)
    session2 = Session(bind=conn2)
    try:
        claimed1 = PublishJobRepository(session1).claim_next(
            now, stale_before=stale_before
        )
        claimed2 = PublishJobRepository(session2).claim_next(
            now, stale_before=stale_before
        )

        winners = [c for c in (claimed1, claimed2) if c is not None]
        assert len(winners) == 1
        assert winners[0].reclaimed is True
        assert winners[0].job.id == job_id
        assert winners[0].job.attempt_count == 2
    finally:
        session1.close()
        session2.close()
        trans1.rollback()
        trans2.rollback()
        conn1.close()
        conn2.close()
        cleanup = Session(bind=engine)
        try:
            cleanup.execute(
                delete(PublishJob).where(PublishJob.tenant_id == tenant_id)
            )
            cleanup.execute(delete(Campaign).where(Campaign.tenant_id == tenant_id))
            cleanup.execute(delete(User).where(User.tenant_id == tenant_id))
            cleanup.execute(delete(Tenant).where(Tenant.id == tenant_id))
            cleanup.commit()
        finally:
            cleanup.close()


def test_repository_reclaim_never_commits(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="no-c")
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=started,
        attempt_count=1,
    )
    job_id = job.id
    now = started + timedelta(hours=1)

    claimed = repo.claim_next(now, stale_before=now - timedelta(seconds=900))
    assert claimed is not None
    db_session.flush()
    db_session.rollback()

    assert db_session.get(PublishJob, job_id) is None


# --- service / observability / UoW -------------------------------------------


def test_run_once_emits_publish_job_reclaimed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "publish_job_stale_after_seconds", 900
    )
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="log")
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=started,
        attempt_count=1,
    )
    db_session.commit()

    fake = _FakePublish()
    service = PublishJobService(
        PublishJobRepository(db_session),
        CampaignRepository(db_session),
        fake,  # type: ignore[arg-type]
        db_session,
    )
    handler = _JsonCaptureHandler()
    job_logger = logging.getLogger("verisure.publish_job")
    job_logger.addHandler(handler)
    job_logger.setLevel(logging.INFO)
    try:
        assert service.run_once() is True
    finally:
        job_logger.removeHandler(handler)

    reclaim_payloads = [
        p for p in handler.payloads if p.get("message") == "publish_job_reclaimed"
    ]
    claim_payloads = [
        p for p in handler.payloads if p.get("message") == "publish_job_claimed"
    ]
    assert len(reclaim_payloads) == 1
    assert claim_payloads == []
    payload = reclaim_payloads[0]
    assert payload["job_id"] == str(job.id)
    assert payload["tenant_id"] == str(tenant.id)
    assert payload["campaign_id"] == str(campaign.id)
    assert payload["attempt_count"] == 2
    assert payload["service"] == "worker"
    assert fake.calls == [(tenant.id, campaign.id, False)]

    db_session.refresh(job)
    assert job.status == PublishJobStatus.SUCCEEDED
    assert job.attempt_count == 2


def test_run_once_reclaim_unit_of_work_commits_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import settings as settings_module

    monkeypatch.setattr(
        settings_module.settings, "publish_job_stale_after_seconds", 900
    )
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="uow")
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=started,
        attempt_count=1,
    )
    db_session.commit()

    tracking = _TrackingSession(db_session)
    service = PublishJobService(
        PublishJobRepository(tracking),  # type: ignore[arg-type]
        CampaignRepository(tracking),  # type: ignore[arg-type]
        _FakePublish(),  # type: ignore[arg-type]
        tracking,  # type: ignore[arg-type]
    )

    assert service.run_once() is True
    assert tracking.commits == 1
    assert tracking.rollbacks == 0

    db_session.refresh(job)
    assert job.status == PublishJobStatus.SUCCEEDED


@pytest.mark.parametrize("invalid_stale", [0, -1, -100, "abc", 1.5])
def test_invalid_stale_after_seconds_is_rejected(invalid_stale: Any) -> None:
    from pydantic import ValidationError

    from app.core.settings import Settings

    with pytest.raises(ValidationError):
        Settings(
            jwt_secret_key="test-secret-key-for-settings-validation",
            publish_job_stale_after_seconds=invalid_stale,
        )


def test_stale_after_seconds_minimum_one_is_accepted() -> None:
    from app.core.settings import Settings

    loaded = Settings(
        jwt_secret_key="test-secret-key-for-settings-validation",
        publish_job_stale_after_seconds=1,
    )
    assert loaded.publish_job_stale_after_seconds == 1


def test_stale_after_seconds_default_is_900() -> None:
    from app.core.settings import Settings

    loaded = Settings(jwt_secret_key="test-secret-key-for-settings-validation")
    assert loaded.publish_job_stale_after_seconds == 900

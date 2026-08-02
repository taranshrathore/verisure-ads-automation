"""Repository-level tests for PublishJobRepository.

Covers tenant-scoped reads, active-job lookup, atomic claim_next
(FOR UPDATE SKIP LOCKED), conditional mark_finished, and transaction
ownership. No service/worker/API code is exercised here.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.publish_job import PublishJob, PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.publish_job_repository import PublishJobRepository
from app.tests.database import get_test_engine


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Publish Job Repo Tenant {suffix}",
        slug=f"publish-job-repo-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"publish-job-repo-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Publish job repo campaign {suffix}",
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


def _make_job(
    db_session: Session,
    tenant: Tenant,
    campaign: Campaign,
    *,
    status: PublishJobStatus = PublishJobStatus.QUEUED,
    requested_by_user_id=None,
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    attempt_count: int = 0,
    error_message: str | None = None,
) -> PublishJob:
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=requested_by_user_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        attempt_count=attempt_count,
        error_message=error_message,
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


# --- create / get ------------------------------------------------------------


def test_create_stages_a_row(repo: PublishJobRepository, db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="create")
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
    )

    repo.create(job)
    db_session.flush()

    loaded = repo.get_by_id(tenant.id, job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.status == PublishJobStatus.QUEUED


def test_get_by_id_is_tenant_scoped(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant_a, user_a, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="get-a"
    )
    tenant_b, _, _ = _make_tenant_user_campaign(db_session, suffix="get-b")
    job = _make_job(
        db_session, tenant_a, campaign_a, requested_by_user_id=user_a.id
    )

    assert repo.get_by_id(tenant_a.id, job.id) is not None
    assert repo.get_by_id(tenant_b.id, job.id) is None


# --- get_active_for_campaign -------------------------------------------------


def test_get_active_for_campaign_returns_queued(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="active-q")
    job = _make_job(
        db_session, tenant, campaign, requested_by_user_id=user.id
    )

    active = repo.get_active_for_campaign(tenant.id, campaign.id)

    assert active is not None
    assert active.id == job.id
    assert active.status == PublishJobStatus.QUEUED


def test_get_active_for_campaign_returns_running(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="active-r")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
    )

    active = repo.get_active_for_campaign(tenant.id, campaign.id)

    assert active is not None
    assert active.id == job.id
    assert active.status == PublishJobStatus.RUNNING


def test_get_active_for_campaign_excludes_terminal_jobs(
    repo: PublishJobRepository, db_session: Session
) -> None:
    now = datetime.now(timezone.utc)
    tenant_ok, user_ok, campaign_ok = _make_tenant_user_campaign(
        db_session, suffix="active-ok"
    )
    _make_job(
        db_session,
        tenant_ok,
        campaign_ok,
        status=PublishJobStatus.SUCCEEDED,
        requested_by_user_id=user_ok.id,
        started_at=now,
        finished_at=now,
        attempt_count=1,
    )
    assert repo.get_active_for_campaign(tenant_ok.id, campaign_ok.id) is None

    tenant_fail, user_fail, campaign_fail = _make_tenant_user_campaign(
        db_session, suffix="active-fail"
    )
    _make_job(
        db_session,
        tenant_fail,
        campaign_fail,
        status=PublishJobStatus.FAILED,
        requested_by_user_id=user_fail.id,
        started_at=now,
        finished_at=now,
        attempt_count=1,
        error_message="done",
    )
    assert repo.get_active_for_campaign(tenant_fail.id, campaign_fail.id) is None


# --- claim_next --------------------------------------------------------------


def test_claim_next_claims_oldest_queued_job(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign_a = _make_tenant_user_campaign(db_session, suffix="claim-old")
    campaign_b = _make_campaign(
        db_session, tenant, user, name="claim-old-b"
    )
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = _make_job(
        db_session,
        tenant,
        campaign_a,
        requested_by_user_id=user.id,
        created_at=base + timedelta(seconds=10),
    )
    older = _make_job(
        db_session,
        tenant,
        campaign_b,
        requested_by_user_id=user.id,
        created_at=base + timedelta(seconds=1),
    )
    del newer

    started_at = datetime.now(timezone.utc)
    claimed = repo.claim_next(started_at)
    db_session.refresh(older)

    assert claimed is not None
    assert claimed.id == older.id
    assert claimed.status == PublishJobStatus.RUNNING
    assert older.status == PublishJobStatus.RUNNING


def test_claim_next_second_worker_gets_different_job(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign_a = _make_tenant_user_campaign(db_session, suffix="claim-2")
    campaign_b = _make_campaign(db_session, tenant, user, name="claim-2-b")
    base = datetime(2026, 1, 2, tzinfo=timezone.utc)
    first = _make_job(
        db_session,
        tenant,
        campaign_a,
        requested_by_user_id=user.id,
        created_at=base,
    )
    second = _make_job(
        db_session,
        tenant,
        campaign_b,
        requested_by_user_id=user.id,
        created_at=base + timedelta(seconds=1),
    )

    claimed_first = repo.claim_next(datetime.now(timezone.utc))
    claimed_second = repo.claim_next(datetime.now(timezone.utc))

    assert claimed_first is not None
    assert claimed_second is not None
    assert claimed_first.id == first.id
    assert claimed_second.id == second.id
    assert claimed_first.id != claimed_second.id


def test_claim_next_returns_none_when_queue_empty(
    repo: PublishJobRepository, db_session: Session
) -> None:
    assert repo.claim_next(datetime.now(timezone.utc)) is None


def test_claim_next_increments_attempt_count_exactly_once(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="claim-att")
    job = _make_job(
        db_session,
        tenant,
        campaign,
        requested_by_user_id=user.id,
        attempt_count=0,
    )

    claimed = repo.claim_next(datetime.now(timezone.utc))
    db_session.refresh(job)

    assert claimed is not None
    assert claimed.attempt_count == 1
    assert job.attempt_count == 1


def test_claim_next_populates_started_at(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="claim-start")
    job = _make_job(
        db_session, tenant, campaign, requested_by_user_id=user.id
    )
    started_at = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

    claimed = repo.claim_next(started_at)
    db_session.refresh(job)

    assert claimed is not None
    assert claimed.started_at == started_at
    assert job.started_at == started_at


def test_claim_next_is_deterministic_by_created_at_then_id(
    repo: PublishJobRepository, db_session: Session
) -> None:
    """When created_at ties, id ASC is the tie-breaker."""
    tenant, user, campaign_a = _make_tenant_user_campaign(db_session, suffix="ord")
    campaign_b = _make_campaign(db_session, tenant, user, name="ord-b")
    tie = datetime(2026, 4, 1, tzinfo=timezone.utc)
    lower_id = uuid4()
    higher_id = uuid4()
    if lower_id > higher_id:
        lower_id, higher_id = higher_id, lower_id

    later_named = PublishJob(
        id=higher_id,
        tenant_id=tenant.id,
        campaign_id=campaign_a.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
        created_at=tie,
        updated_at=tie,
    )
    earlier_named = PublishJob(
        id=lower_id,
        tenant_id=tenant.id,
        campaign_id=campaign_b.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
        created_at=tie,
        updated_at=tie,
    )
    # Insert higher id first so insertion order cannot explain the claim order.
    repo.create(later_named)
    repo.create(earlier_named)
    db_session.flush()

    claimed = repo.claim_next(datetime.now(timezone.utc))

    assert claimed is not None
    assert claimed.id == lower_id


def test_claim_next_skip_locked_gives_different_jobs_to_concurrent_workers() -> None:
    """Two open transactions each call claim_next; SKIP LOCKED ensures the
    second worker does not wait on (or steal) the first worker's locked
    candidate. No sleeps -- the first claim holds its row lock until its
    transaction ends.
    """
    engine = get_test_engine()
    suffix = uuid4().hex[:10]
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)

    setup = Session(bind=engine)
    try:
        tenant = Tenant(
            name=f"Skip Locked Tenant {suffix}",
            slug=f"skip-locked-{suffix}",
        )
        setup.add(tenant)
        setup.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"skip-locked-{suffix}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
        )
        setup.add(user)
        setup.flush()
        campaign_a = Campaign(
            tenant_id=tenant.id,
            created_by_user_id=user.id,
            name=f"skip-locked-a-{suffix}",
        )
        campaign_b = Campaign(
            tenant_id=tenant.id,
            created_by_user_id=user.id,
            name=f"skip-locked-b-{suffix}",
        )
        setup.add_all([campaign_a, campaign_b])
        setup.flush()

        job_old = PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign_a.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
            created_at=base,
            updated_at=base,
        )
        job_new = PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign_b.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
            created_at=base + timedelta(seconds=1),
            updated_at=base + timedelta(seconds=1),
        )
        setup.add_all([job_old, job_new])
        setup.commit()
        tenant_id = tenant.id
        job_old_id = job_old.id
        job_new_id = job_new.id
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
            datetime.now(timezone.utc)
        )
        claimed2 = PublishJobRepository(session2).claim_next(
            datetime.now(timezone.utc)
        )

        assert claimed1 is not None
        assert claimed2 is not None
        assert claimed1.id == job_old_id
        assert claimed2.id == job_new_id
        assert claimed1.id != claimed2.id
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


# --- mark_finished -----------------------------------------------------------


def test_mark_finished_succeeds(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-ok")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
    )
    finished_at = now + timedelta(seconds=5)

    affected = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        finished_at,
    )
    db_session.refresh(job)

    assert affected == 1
    assert job.status == PublishJobStatus.SUCCEEDED
    assert job.finished_at == finished_at


def test_mark_finished_stale_expected_status_returns_rowcount_zero(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-stale")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
    )

    first = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.FAILED,
        now,
        error_message="boom",
    )
    second = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        now + timedelta(seconds=1),
    )
    db_session.refresh(job)

    assert first == 1
    assert second == 0
    assert job.status == PublishJobStatus.FAILED
    assert job.error_message == "boom"


def test_mark_finished_updates_updated_at(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-upd")
    created = datetime(2026, 7, 1, tzinfo=timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        created_at=created,
        started_at=created,
        attempt_count=1,
    )
    finished_at = created + timedelta(hours=1)

    repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        finished_at,
    )
    db_session.refresh(job)

    assert job.updated_at == finished_at
    assert job.finished_at == finished_at


def test_mark_finished_cross_tenant_returns_rowcount_zero(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant_a, user_a, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="finish-xa"
    )
    tenant_b, _, _ = _make_tenant_user_campaign(db_session, suffix="finish-xb")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant_a,
        campaign_a,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user_a.id,
        started_at=now,
        attempt_count=1,
    )

    affected = repo.mark_finished(
        tenant_b.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        now,
    )
    db_session.refresh(job)

    assert affected == 0
    assert job.status == PublishJobStatus.RUNNING


def test_mark_finished_omitting_error_message_does_not_clobber(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-omit")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
        error_message="pre-existing",
    )

    affected = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.FAILED,
        now,
    )
    db_session.refresh(job)

    assert affected == 1
    assert job.error_message == "pre-existing"


def test_mark_finished_explicit_none_clears_error_message(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-clr")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
        error_message="clear-me",
    )

    affected = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        now,
        error_message=None,
    )
    db_session.refresh(job)

    assert affected == 1
    assert job.error_message is None


def test_mark_finished_stale_expected_against_terminal_is_no_op(
    repo: PublishJobRepository, db_session: Session
) -> None:
    """A finish call still expecting RUNNING cannot mutate a terminal row.

    Transition legality (e.g. forbidding SUCCEEDED->FAILED) is a service
    concern; this layer only guarantees the expected_current_status
    predicate.
    """
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="finish-term")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.SUCCEEDED,
        requested_by_user_id=user.id,
        started_at=now,
        finished_at=now,
        attempt_count=1,
    )

    affected = repo.mark_finished(
        tenant.id,
        job.id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.FAILED,
        now + timedelta(seconds=2),
        error_message="should-not-apply",
    )
    db_session.refresh(job)

    assert affected == 0
    assert job.status == PublishJobStatus.SUCCEEDED
    assert job.error_message is None
    assert job.finished_at == now


def test_claim_next_does_not_reclaim_running_job(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="no-reclaim")
    now = datetime.now(timezone.utc)
    job = _make_job(
        db_session,
        tenant,
        campaign,
        status=PublishJobStatus.RUNNING,
        requested_by_user_id=user.id,
        started_at=now,
        attempt_count=1,
    )

    claimed = repo.claim_next(now + timedelta(seconds=30))
    db_session.refresh(job)

    assert claimed is None
    assert job.attempt_count == 1
    assert job.started_at == now


def test_claim_next_returning_synchronizes_identity_mapped_instance(
    repo: PublishJobRepository, db_session: Session
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="orm-sync")
    job = _make_job(
        db_session, tenant, campaign, requested_by_user_id=user.id
    )
    started_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

    claimed = repo.claim_next(started_at)

    assert claimed is job
    assert job.status == PublishJobStatus.RUNNING
    assert job.attempt_count == 1
    assert job.started_at == started_at


# --- transaction ownership ---------------------------------------------------


def test_repository_methods_never_commit(
    repo: PublishJobRepository, db_session: Session
) -> None:
    """create/claim_next/mark_finished only stage/execute writes -- none
    call session.commit(). Rolling back the session afterward discards
    everything; if any method had committed internally, this rollback
    would not undo it under the savepoint-based db_session fixture.
    """
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="no-commit")
    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
    )
    repo.create(job)
    db_session.flush()
    job_id = job.id

    repo.claim_next(datetime.now(timezone.utc))
    db_session.flush()
    repo.mark_finished(
        tenant.id,
        job_id,
        PublishJobStatus.RUNNING,
        PublishJobStatus.SUCCEEDED,
        datetime.now(timezone.utc),
    )
    db_session.flush()

    db_session.rollback()

    assert db_session.get(PublishJob, job_id) is None

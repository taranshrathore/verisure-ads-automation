"""Database-level constraint tests for the publish_jobs table.

These exercise real PostgreSQL CHECK/UNIQUE/FK/enum constraints directly
via the ORM. No repository or service exists yet (Phase 1 scope is model
+ migration only), so every row here is constructed and flushed directly.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.publish_job import PublishJob, PublishJobStatus
from app.models.tenant import Tenant
from app.models.user import User


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Publish Job Tenant {suffix}", slug=f"publish-job-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"publish-job-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()

    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Publish job campaign {suffix}",
    )
    db_session.add(campaign)
    db_session.flush()
    return tenant, user, campaign


def test_valid_queued_row(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="queued")

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.flush()

    assert job.status == PublishJobStatus.QUEUED
    assert job.attempt_count == 0
    assert job.started_at is None
    assert job.finished_at is None


def test_valid_running_row(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="running")
    now = datetime.now(timezone.utc)

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.RUNNING,
        started_at=now,
        attempt_count=1,
    )
    db_session.add(job)
    db_session.flush()

    assert job.status == PublishJobStatus.RUNNING
    assert job.started_at is not None
    assert job.attempt_count == 1


def test_valid_succeeded_row(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="succeeded")
    now = datetime.now(timezone.utc)

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        attempt_count=1,
    )
    db_session.add(job)
    db_session.flush()

    assert job.status == PublishJobStatus.SUCCEEDED
    assert job.finished_at is not None


def test_valid_failed_row(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="failed")
    now = datetime.now(timezone.utc)

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.FAILED,
        started_at=now,
        finished_at=now,
        error_message="simulated job failure",
        attempt_count=1,
    )
    db_session.add(job)
    db_session.flush()

    assert job.status == PublishJobStatus.FAILED
    assert job.error_message == "simulated job failure"


def test_negative_attempt_count_is_rejected(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="neg")

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.QUEUED,
        attempt_count=-1,
    )
    db_session.add(job)

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_partial_unique_index_blocks_second_queued_job(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="dup-q")

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
        )
    )
    db_session.flush()

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_partial_unique_index_blocks_queued_while_running(
    db_session: Session,
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="dup-r")

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            attempt_count=1,
        )
    )
    db_session.flush()

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_partial_unique_index_blocks_second_running_job(
    db_session: Session,
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="dup-rr")
    now = datetime.now(timezone.utc)

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.RUNNING,
            started_at=now,
            attempt_count=1,
        )
    )
    db_session.flush()

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.RUNNING,
            started_at=now,
            attempt_count=1,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_cross_tenant_campaign_composite_fk_is_rejected(db_session: Session) -> None:
    """A job cannot pair tenant A's tenant_id with tenant B's campaign_id --
    enforced by the composite FK, not only by future service-layer checks.
    """
    tenant_a, user_a, _campaign_a = _make_tenant_user_campaign(
        db_session, suffix="fk-a"
    )
    _tenant_b, _user_b, campaign_b = _make_tenant_user_campaign(
        db_session, suffix="fk-b"
    )

    db_session.add(
        PublishJob(
            tenant_id=tenant_a.id,
            campaign_id=campaign_b.id,
            requested_by_user_id=user_a.id,
            status=PublishJobStatus.QUEUED,
        )
    )

    with pytest.raises(IntegrityError):
        db_session.flush()


def test_error_message_accepts_2000_characters(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="err-ok")
    now = datetime.now(timezone.utc)
    message = "x" * 2000

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.FAILED,
        started_at=now,
        finished_at=now,
        error_message=message,
        attempt_count=1,
    )
    db_session.add(job)
    db_session.flush()

    assert job.error_message is not None
    assert len(job.error_message) == 2000


def test_error_message_rejects_over_2000_characters(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="err-long")
    now = datetime.now(timezone.utc)

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.FAILED,
        started_at=now,
        finished_at=now,
        error_message="y" * 2001,
        attempt_count=1,
    )
    db_session.add(job)

    with pytest.raises(DataError):
        db_session.flush()


def test_null_requested_by_user_id_is_accepted(db_session: Session) -> None:
    tenant, _user, campaign = _make_tenant_user_campaign(db_session, suffix="null-user")

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=None,
        status=PublishJobStatus.QUEUED,
    )
    db_session.add(job)
    db_session.flush()

    assert job.requested_by_user_id is None


def test_succeeded_job_allows_another_queued_job(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="after-ok")
    now = datetime.now(timezone.utc)

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            attempt_count=1,
        )
    )
    db_session.flush()

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
        )
    )
    db_session.flush()  # does not raise


def test_failed_job_allows_another_queued_job(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="after-fail")
    now = datetime.now(timezone.utc)

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.FAILED,
            started_at=now,
            finished_at=now,
            error_message="prior failure",
            attempt_count=1,
        )
    )
    db_session.flush()

    db_session.add(
        PublishJob(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
            status=PublishJobStatus.QUEUED,
        )
    )
    db_session.flush()  # does not raise


def test_same_active_status_across_different_tenants_is_allowed(
    db_session: Session,
) -> None:
    tenant_a, user_a, campaign_a = _make_tenant_user_campaign(
        db_session, suffix="ten-a"
    )
    tenant_b, user_b, campaign_b = _make_tenant_user_campaign(
        db_session, suffix="ten-b"
    )

    db_session.add(
        PublishJob(
            tenant_id=tenant_a.id,
            campaign_id=campaign_a.id,
            requested_by_user_id=user_a.id,
            status=PublishJobStatus.QUEUED,
        )
    )
    db_session.add(
        PublishJob(
            tenant_id=tenant_b.id,
            campaign_id=campaign_b.id,
            requested_by_user_id=user_b.id,
            status=PublishJobStatus.QUEUED,
        )
    )
    db_session.flush()  # does not raise


def test_status_enum_stored_as_lowercase_database_value(db_session: Session) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enum")

    job = PublishJob(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
        status=PublishJobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        attempt_count=1,
    )
    db_session.add(job)
    db_session.flush()

    raw_status = db_session.execute(
        text("SELECT status::text FROM publish_jobs WHERE id = :id"),
        {"id": job.id},
    ).scalar_one()

    assert raw_status == "running"

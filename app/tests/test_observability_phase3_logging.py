"""Observability Foundation Phase 3: lifecycle logging tests.

Caplog-only assertions. No sleeps. No credential material in logs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec
from app.core.logging import JsonLogFormatter
from app.core.logging_context import bound_context, clear, get_context
from app.core.provider_credentials import ProviderCredentials
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.campaign import (
    Campaign,
    CampaignBudgetType,
    CampaignObjective,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.orchestration import publish_worker
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.repositories.publish_job_repository import PublishJobRepository
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.provider_connection_service import ProviderConnectionService
from app.services.publish_campaign_service import PublishCampaignService
from app.services.publish_job_service import (
    PublishJobService,
    consume_business_failure_logged,
)

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@pytest.fixture(autouse=True)
def _clean_log_context() -> None:
    clear()
    consume_business_failure_logged()
    yield
    clear()
    consume_business_failure_logged()


class _SessionCloseGuard:
    """Delegates to the test session but ignores close() from the worker."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _make_tenant_user_campaign(
    db_session: Session, *, suffix: str
) -> tuple[Tenant, User, Campaign]:
    tenant = Tenant(
        name=f"Obs P3 Tenant {suffix}",
        slug=f"obs-p3-{suffix}",
    )
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"obs-p3-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    campaign = Campaign(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name=f"Obs P3 campaign {suffix}",
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


class _FakePublish:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls: list[tuple] = []

    def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
        self.calls.append((tenant_id, campaign_id))
        if self.error is not None:
            raise self.error
        return []


class _FakeSuccessAdapter(BaseAdapter):
    def __init__(self, external_id: str) -> None:
        self._external_id = external_id

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del spec, credentials
        return PublishResult(
            success=True,
            external_campaign_id=self._external_id,
            error_message=None,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError


class _FakeRegistry:
    def __init__(self, adapters: dict[Provider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: Provider) -> BaseAdapter:
        return self._adapters[provider]


def _job_service(
    db_session: Session, publish: _FakePublish | None = None
) -> PublishJobService:
    return PublishJobService(
        PublishJobRepository(db_session),
        CampaignRepository(db_session),
        publish or _FakePublish(),  # type: ignore[arg-type]
        db_session,
    )


def _records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.message == message]


class _JsonCaptureHandler(logging.Handler):
    """Format immediately so contextvars fields are present on the payload."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonLogFormatter())
        self.payloads: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def _payloads_for(handler: _JsonCaptureHandler, message: str) -> list[dict[str, Any]]:
    return [p for p in handler.payloads if p.get("message") == message]


def test_publish_job_enqueued_emitted(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="enq")
    service = _job_service(db_session)
    with bound_context(request_id="req-obs-1"), caplog.at_level(
        logging.INFO, logger="verisure.publish_job"
    ):
        job = service.enqueue(
            tenant_id=tenant.id,
            campaign_id=campaign.id,
            requested_by_user_id=user.id,
        )
    assert len(_records(caplog, "publish_job_enqueued")) == 1
    assert job.status.value == "queued"


def test_publish_job_claimed_and_succeeded_emitted(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="ok")
    service = _job_service(db_session)
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    with caplog.at_level(logging.INFO, logger="verisure.publish_job"):
        assert service.run_once() is True
    assert len(_records(caplog, "publish_job_claimed")) == 1
    assert len(_records(caplog, "publish_job_succeeded")) == 1
    assert len(_records(caplog, "publish_job_failed")) == 0


def test_publish_job_failed_emitted_exactly_once(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="fail")
    service = _job_service(
        db_session, publish=_FakePublish(error=RuntimeError("adapter boom"))
    )
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    with caplog.at_level(logging.INFO, logger="verisure.publish_job"):
        with pytest.raises(RuntimeError, match="adapter boom"):
            service.run_once()
    assert len(_records(caplog, "publish_job_failed")) == 1
    assert len(_records(caplog, "publish_job_succeeded")) == 0
    assert consume_business_failure_logged() is True


def test_campaign_and_provider_lifecycle_logs(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="pub")
    encryption = CredentialEncryptionService(_TEST_ENCRYPTION_KEY)
    connections = ProviderConnectionService(
        ProviderConnectionRepository(db_session), encryption, db_session
    )
    for provider in (Provider.META, Provider.GOOGLE):
        connections.connect(
            tenant_id=tenant.id,
            provider=provider,
            credential_payload=b"secret-should-never-appear",
        )
    registry = _FakeRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-1"),
        }
    )
    publish = PublishCampaignService(
        CampaignRepository(db_session),
        CampaignDeploymentRepository(db_session),
        CampaignDeploymentService(
            CampaignDeploymentRepository(db_session), db_session
        ),
        CampaignSpecBuilder(),
        registry,  # type: ignore[arg-type]
        connections,
        db_session,
    )
    with bound_context(job_id="job-from-worker"), caplog.at_level(
        logging.INFO, logger="verisure.publish_campaign"
    ):
        deployments = publish.publish_campaign(
            tenant_id=tenant.id, campaign_id=campaign.id
        )
    assert len(deployments) == 2
    assert len(_records(caplog, "campaign_publish_started")) == 1
    assert len(_records(caplog, "provider_publish_finished")) == 2
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-should-never-appear" not in text
    assert "credential_payload" not in text


def test_worker_started_idle_and_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}

    class _JobService:
        def run_once(self) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                return False
            if calls["n"] == 2:
                raise RuntimeError("infra boom")
            return False

    class _Session:
        def close(self) -> None:
            return None

    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: _Session())
    )
    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _s: _JobService()
    )
    sleeps: list[float] = []

    def _sleep(_seconds: float) -> None:
        sleeps.append(_seconds)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(publish_worker.time, "sleep", _sleep)
    monkeypatch.setattr(
        publish_worker.settings, "publish_job_poll_interval_seconds", 5, raising=False
    )

    with caplog.at_level(logging.DEBUG, logger="verisure.worker"):
        with pytest.raises(KeyboardInterrupt):
            publish_worker.run_forever()

    assert len(_records(caplog, "worker_started")) == 1
    assert len(_records(caplog, "worker_idle")) >= 1
    for record in _records(caplog, "worker_idle"):
        assert record.levelno == logging.DEBUG
    assert len(_records(caplog, "unexpected_worker_error")) == 1


def test_worker_does_not_duplicate_business_failure_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="nodup")
    service = _job_service(
        db_session, publish=_FakePublish(error=RuntimeError("biz fail"))
    )
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )

    guarded = _SessionCloseGuard(db_session)
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: guarded)
    )
    monkeypatch.setattr(
        publish_worker, "build_publish_job_service", lambda _s: service
    )

    def _sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(publish_worker.time, "sleep", _sleep)

    with caplog.at_level(logging.INFO):
        with pytest.raises(KeyboardInterrupt):
            publish_worker.run_forever()

    assert len(_records(caplog, "publish_job_failed")) == 1
    assert len(_records(caplog, "unexpected_worker_error")) == 0
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_repositories_remain_silent(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    tenant, _user, campaign = _make_tenant_user_campaign(db_session, suffix="repo")
    repo = PublishJobRepository(db_session)
    with caplog.at_level(logging.DEBUG):
        assert repo.get_active_for_campaign(tenant.id, campaign.id) is None
        CampaignRepository(db_session).get_by_tenant_and_id(tenant.id, campaign.id)
    assert caplog.records == []


def test_context_bound_during_claim_and_cleared_after_worker_run_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="ctx")
    service = _job_service(db_session)
    service.enqueue(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        requested_by_user_id=user.id,
    )
    seen: dict[str, Any] = {}

    class _TrackingPublish:
        def publish_campaign(self, *, tenant_id, campaign_id, commit: bool = True):
            seen.update(get_context())
            return []

    service_with_tracking = PublishJobService(
        PublishJobRepository(db_session),
        CampaignRepository(db_session),
        _TrackingPublish(),  # type: ignore[arg-type]
        db_session,
    )
    guarded = _SessionCloseGuard(db_session)
    monkeypatch.setattr(
        publish_worker, "SessionFactory", lambda: (lambda: guarded)
    )
    monkeypatch.setattr(
        publish_worker,
        "build_publish_job_service",
        lambda _s: service_with_tracking,
    )
    assert publish_worker.run_once() is True
    assert seen.get("job_id") is not None
    assert seen.get("tenant_id") == str(tenant.id)
    assert seen.get("campaign_id") == str(campaign.id)
    assert get_context() == {}


def test_enqueued_log_includes_request_id_from_context(
    db_session: Session,
) -> None:
    tenant, user, campaign = _make_tenant_user_campaign(db_session, suffix="rid")
    service = _job_service(db_session)
    handler = _JsonCaptureHandler()
    job_logger = logging.getLogger("verisure.publish_job")
    job_logger.addHandler(handler)
    job_logger.setLevel(logging.INFO)
    try:
        with bound_context(request_id="http-req-99"):
            service.enqueue(
                tenant_id=tenant.id,
                campaign_id=campaign.id,
                requested_by_user_id=user.id,
            )
    finally:
        job_logger.removeHandler(handler)

    payloads = _payloads_for(handler, "publish_job_enqueued")
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["request_id"] == "http-req-99"
    assert payload["service"] == "api"
    assert payload["tenant_id"] == str(tenant.id)
    assert payload["campaign_id"] == str(campaign.id)
    assert payload["status"] == "queued"
    assert payload["job_id"]

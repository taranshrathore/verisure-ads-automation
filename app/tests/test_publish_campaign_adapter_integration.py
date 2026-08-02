"""Adapter-dispatch tests for PublishCampaignService (Milestone 5).

Verifies the wiring between PublishCampaignService and the Milestone 4
adapter layer using tiny fake adapters -- no real Meta/Google API, no
OAuth, no HTTP, no network. Fakes are plain BaseAdapter subclasses
constructed directly and handed to PublishCampaignService through a
small duck-typed fake registry that matches
ProviderAdapterRegistry.get's interface (get(provider) -> BaseAdapter).
Nothing here monkeypatches ProviderAdapterRegistry or
PublishCampaignService internals -- the fake registry is a plain
substitute object passed in through the constructor, exactly like any
other dependency in this codebase.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.core.campaign_spec import CampaignSpec
from app.core.provider_credentials import ProviderCredentials
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective
from app.models.campaign_deployment import CampaignDeployment, CampaignDeploymentStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.provider_connection_repository import (
    ProviderConnectionRepository,
)
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.provider_connection_service import ProviderConnectionService
from app.services.publish_campaign_service import PublishCampaignService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")
_DEFAULT_CREDENTIAL_PAYLOAD = b"opaque-adapter-integ-credential"

# --- Fake adapters -------------------------------------------------------------


class FakeSuccessAdapter(BaseAdapter):
    """Always reports a successful publish with a fixed external ID."""

    def __init__(self, external_campaign_id: str) -> None:
        self._external_campaign_id = external_campaign_id

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del credentials
        return PublishResult(
            success=True,
            external_campaign_id=self._external_campaign_id,
            error_message=None,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")


class FakeFailureAdapter(BaseAdapter):
    """Always reports a provider-declined publish (no exception raised)."""

    def __init__(self, error_message: str) -> None:
        self._error_message = error_message

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del credentials
        return PublishResult(
            success=False,
            external_campaign_id=None,
            error_message=self._error_message,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")


class FakeExceptionAdapter(BaseAdapter):
    """Simulates an adapter bug: raises instead of returning a PublishResult.

    Raises exception_to_raise if supplied, else a RuntimeError with a
    fixed message -- letting tests exercise, e.g., an exception whose
    str() is blank (test_blank_exception_message_gets_a_safe_fallback).
    """

    def __init__(self, exception_to_raise: Exception | None = None) -> None:
        self._exception_to_raise = exception_to_raise or RuntimeError(
            "simulated adapter crash"
        )

    def publish(
        self, spec: CampaignSpec, credentials: ProviderCredentials
    ) -> PublishResult:
        del credentials
        raise self._exception_to_raise

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by these tests.")


class FakeAdapterRegistry:
    """Duck-typed stand-in for ProviderAdapterRegistry.

    Implements the same get(provider) -> BaseAdapter surface, backed by
    a plain dict supplied by the test -- not a subclass of, and never
    reaching into, the real ProviderAdapterRegistry.
    """

    def __init__(self, adapters: dict[Provider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: Provider) -> BaseAdapter:
        try:
            return self._adapters[provider]
        except KeyError:
            raise ValueError(
                f"No fake adapter registered for provider: {provider!r}"
            ) from None


# --- Fixtures / helpers ---------------------------------------------------------


@pytest.fixture
def deployment_repository(db_session: Session) -> CampaignDeploymentRepository:
    return CampaignDeploymentRepository(db_session)


@pytest.fixture
def deployment_service(
    deployment_repository: CampaignDeploymentRepository, db_session: Session
) -> CampaignDeploymentService:
    return CampaignDeploymentService(deployment_repository, db_session)


@pytest.fixture
def connection_service(db_session: Session) -> ProviderConnectionService:
    return ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        CredentialEncryptionService(_TEST_ENCRYPTION_KEY),
        db_session,
    )


def _connect_supported_providers(
    connection_service: ProviderConnectionService,
    *,
    tenant_id,
    credential_payload: bytes = _DEFAULT_CREDENTIAL_PAYLOAD,
) -> None:
    for provider in (Provider.META, Provider.GOOGLE):
        connection_service.connect(
            tenant_id=tenant_id,
            provider=provider,
            credential_payload=credential_payload,
        )


def _make_publish_service(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
    adapter_registry: FakeAdapterRegistry,
) -> PublishCampaignService:
    return PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        deployment_service,
        CampaignSpecBuilder(),
        adapter_registry,  # type: ignore[arg-type]
        connection_service,
        db_session,
    )


def _make_tenant_and_user(db_session: Session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"Adapter Integ Tenant {suffix}", slug=f"adapter-integ-{suffix}"
    )
    db_session.add(tenant)
    db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"adapter-integ-{suffix}@example.com",
        hashed_password="not-a-real-password-hash",
        role="member",
    )
    db_session.add(user)
    db_session.flush()
    return tenant, user


def _make_complete_campaign(
    db_session: Session, tenant: Tenant, user: User, **overrides: object
) -> Campaign:
    """A campaign with objective/budget/schedule all set, so
    CampaignSpecBuilder.build succeeds and the fake adapter is actually
    reached (an incomplete campaign would fail spec-building before the
    adapter is ever called).
    """
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults: dict[str, object] = dict(
        tenant_id=tenant.id,
        created_by_user_id=user.id,
        name="Adapter integration campaign",
        objective=CampaignObjective.CONVERSIONS,
        budget_type=CampaignBudgetType.DAILY,
        budget_amount=Decimal("75.00"),
        currency="USD",
        start_at=now,
        end_at=now + timedelta(days=30),
    )
    defaults.update(overrides)
    campaign = Campaign(**defaults)
    db_session.add(campaign)
    db_session.flush()
    return campaign


def _by_provider(
    deployments: list, provider: Provider
):
    return next(d for d in deployments if d.provider == provider)


# --- Both providers succeed -----------------------------------------------------


def test_both_providers_succeed(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="a")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeSuccessAdapter("meta-ext-1"),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-1"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    assert len(deployments) == 2
    for deployment in deployments:
        assert deployment.status == CampaignDeploymentStatus.SUBMITTED

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.external_campaign_id == "meta-ext-1"
    assert google.external_campaign_id == "google-ext-1"


# --- One succeeds, one fails ----------------------------------------------------


def test_one_provider_succeeds_and_one_fails(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="b")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeFailureAdapter(
                "meta rejected the budget"
            ),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-2"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message == "meta rejected the budget"
    assert meta.external_campaign_id is None
    assert google.status == CampaignDeploymentStatus.SUBMITTED
    assert google.external_campaign_id == "google-ext-2"


# --- Adapter raises unexpectedly ------------------------------------------------


def test_adapter_exception_is_caught_and_marks_deployment_failed(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    tenant, user = _make_tenant_and_user(db_session, suffix="c")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeExceptionAdapter(),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-3"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    # No exception escapes publish_campaign even though META's adapter raises.
    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message == "simulated adapter crash"


def test_google_is_still_attempted_when_meta_adapter_raises(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """META is iterated before GOOGLE (see _SUPPORTED_PROVIDERS); this
    proves an exception from META's adapter does not short-circuit the
    loop before GOOGLE is attempted.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="d")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeExceptionAdapter(),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-4"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    providers_seen = {d.provider for d in deployments}
    assert providers_seen == {
        Provider.META,
        Provider.GOOGLE,
    }
    google = _by_provider(deployments, Provider.GOOGLE)
    assert google.status == CampaignDeploymentStatus.SUBMITTED
    assert google.external_campaign_id == "google-ext-4"


def test_google_is_still_attempted_when_meta_adapter_reports_failure(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """Same as above, but META fails via an ordinary PublishResult(success=False)
    instead of raising -- both failure modes must still let GOOGLE proceed.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="e")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeFailureAdapter("meta declined"),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-5"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    google = _by_provider(deployments, Provider.GOOGLE)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert google.status == CampaignDeploymentStatus.SUBMITTED
    assert google.external_campaign_id == "google-ext-5"


# --- Error-message safety (Milestone 5 audit) -----------------------------------


def test_blank_exception_message_gets_a_safe_fallback(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """str(RuntimeError("")) == "" -- last_error_message must never end up
    blank just because the adapter's exception message was.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="f")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeExceptionAdapter(RuntimeError("")),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-6"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message is not None
    assert meta.last_error_message.strip() != ""
    assert "RuntimeError" in meta.last_error_message


def test_overly_long_error_message_is_truncated_to_column_width(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """last_error_message is String(2000) -- see
    app/models/campaign_deployment.py -- so a longer message must be
    truncated rather than causing a database error.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="g")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    huge_message = "x" * 5000
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeFailureAdapter(huge_message),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-7"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    assert meta.status == CampaignDeploymentStatus.FAILED
    assert meta.last_error_message is not None
    assert len(meta.last_error_message) <= 2000
    assert meta.last_error_message.endswith("...[truncated]")


# --- Persistence failures must propagate, not be swallowed ---------------------


class _MarkFailedAlwaysRaisesDeploymentService:
    """Duck-typed stand-in for CampaignDeploymentService that delegates
    everything to a real instance except mark_failed, which always
    raises -- simulating a persistence/database failure specifically
    while recording a provider failure.
    """

    def __init__(self, real_service: CampaignDeploymentService) -> None:
        self._real = real_service

    def create_pending_deployment(self, **kwargs: object) -> CampaignDeployment:
        return self._real.create_pending_deployment(**kwargs)  # type: ignore[arg-type]

    def mark_submitted(self, **kwargs: object) -> CampaignDeployment:
        return self._real.mark_submitted(**kwargs)  # type: ignore[arg-type]

    def mark_failed(self, **kwargs: object) -> CampaignDeployment:
        raise RuntimeError("simulated persistence failure while recording a failure")


def test_persistence_failure_while_recording_a_failure_propagates(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """A real database/persistence failure while recording META's failure
    must propagate out of publish_campaign as-is, never be swallowed or
    mislabeled as an ordinary provider failure.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="h")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeFailureAdapter("meta declined"),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-8"),
        }
    )
    failing_deployment_service = _MarkFailedAlwaysRaisesDeploymentService(
        deployment_service
    )
    service = PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        failing_deployment_service,  # type: ignore[arg-type]
        CampaignSpecBuilder(),
        registry,
        connection_service,
        db_session,
    )

    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)


def test_next_provider_is_not_attempted_when_failure_persistence_fails(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
) -> None:
    """publish_campaign's per-provider loop both prepares (create-if-
    missing) and dispatches a provider in the same iteration -- it does
    not prepare every provider's deployment upfront before dispatching
    any of them. So when META's own failure-recording raises, the loop
    stops immediately: GOOGLE's iteration -- including its own "create
    deployment if missing" step -- is never reached at all, and no
    GOOGLE deployment row exists afterward.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix="i")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)
    registry = FakeAdapterRegistry(
        {
            Provider.META: FakeFailureAdapter("meta declined"),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-9"),
        }
    )
    failing_deployment_service = _MarkFailedAlwaysRaisesDeploymentService(
        deployment_service
    )
    service = PublishCampaignService(
        CampaignRepository(db_session),
        deployment_repository,
        failing_deployment_service,  # type: ignore[arg-type]
        CampaignSpecBuilder(),
        registry,
        connection_service,
        db_session,
    )

    with pytest.raises(RuntimeError):
        service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    google_deployment = deployment_repository.get_by_campaign_and_provider(
        tenant.id, campaign.id, Provider.GOOGLE
    )
    assert google_deployment is None


# --- Non-PENDING deployments are never republished ------------------------------


@pytest.mark.parametrize(
    "target_status",
    [
        CampaignDeploymentStatus.LIVE,
        CampaignDeploymentStatus.PAUSED,
        CampaignDeploymentStatus.FAILED,
    ],
)
def test_non_pending_deployment_is_not_republished(
    deployment_repository: CampaignDeploymentRepository,
    deployment_service: CampaignDeploymentService,
    connection_service: ProviderConnectionService,
    db_session: Session,
    target_status: CampaignDeploymentStatus,
) -> None:
    """A deployment already in LIVE/PAUSED/FAILED must be returned exactly
    as found -- publish_campaign must never call the adapter for it,
    regardless of what the (in this test, guaranteed-unreached) fake
    adapter would do.
    """
    tenant, user = _make_tenant_and_user(db_session, suffix=f"j-{target_status.value}")
    campaign = _make_complete_campaign(db_session, tenant, user)
    _connect_supported_providers(connection_service, tenant_id=tenant.id)

    pending = deployment_service.create_pending_deployment(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        provider=Provider.META,
    )
    submitted = deployment_service.mark_submitted(
        tenant_id=tenant.id,
        deployment_id=pending.id,
        external_campaign_id="pre-existing-ext-id",
    )
    if target_status == CampaignDeploymentStatus.LIVE:
        deployment_service.mark_live(tenant_id=tenant.id, deployment_id=submitted.id)
    elif target_status == CampaignDeploymentStatus.PAUSED:
        deployment_service.mark_live(tenant_id=tenant.id, deployment_id=submitted.id)
        deployment_service.mark_paused(tenant_id=tenant.id, deployment_id=submitted.id)
    elif target_status == CampaignDeploymentStatus.FAILED:
        deployment_service.mark_failed(
            tenant_id=tenant.id,
            deployment_id=submitted.id,
            last_error_message="pre-existing failure",
        )

    # An adapter that would raise if ever called -- proves it is never called.
    class _AdapterThatMustNeverBeCalled(FakeExceptionAdapter):
        pass

    registry = FakeAdapterRegistry(
        {
            Provider.META: _AdapterThatMustNeverBeCalled(),
            Provider.GOOGLE: FakeSuccessAdapter("google-ext-10"),
        }
    )
    service = _make_publish_service(
        deployment_repository, deployment_service, connection_service, db_session, registry
    )

    deployments = service.publish_campaign(tenant_id=tenant.id, campaign_id=campaign.id)

    meta = _by_provider(deployments, Provider.META)
    assert meta.id == pending.id
    assert meta.status == target_status
    assert meta.external_campaign_id == "pre-existing-ext-id"

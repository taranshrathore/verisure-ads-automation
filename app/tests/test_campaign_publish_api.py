"""API tests for campaign publish + deployment listing.

MILESTONE 6 SCOPE: this only tests the HTTP + dependency-injection
wiring added on top of the already-built PublishCampaignService /
CampaignDeploymentService -- no new domain logic is under test here
(see test_publish_campaign_service.py and
test_publish_campaign_adapter_integration.py for that). Every route
enforces authentication only (Depends(get_current_user)); there is no
local RBAC check -- matching the rest of app/api/v1/campaigns.py.

Real adapters (MetaAdapter/GoogleAdapter) still raise
NotImplementedError -- see app/adapters/ -- so tests that exercise the
real, unmodified ProviderAdapterRegistry expect every deployment to
end up FAILED, not SUBMITTED/LIVE. One focused test injects a fake,
always-successful adapter registry via a FastAPI dependency override
(app.dependency_overrides), rather than monkeypatching internals, to
verify the success-path response shape end to end.
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.api.dependencies import get_provider_adapter_registry
from app.core.campaign_spec import CampaignSpec
from app.main import app
from app.models.campaign_deployment import CampaignDeploymentProvider
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.services.campaign_deployment_service import CampaignDeploymentService

CAMPAIGNS_URL = "/api/v1/campaigns"
_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_campaign(client: TestClient, token: str, **overrides: object) -> dict:
    payload: dict[str, object] = {"name": "Publish API Test Campaign"}
    payload.update(overrides)
    response = client.post(CAMPAIGNS_URL, json=payload, headers=_auth_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


def _create_complete_campaign(
    client: TestClient, token: str, **overrides: object
) -> dict:
    """A campaign with objective/budget/schedule all set, so
    CampaignSpecBuilder.build succeeds and a real (or fake) adapter is
    actually reached -- an incomplete campaign would instead fail
    spec-building before any adapter is ever called, which would still
    produce a FAILED deployment but for the wrong reason for these
    tests' purposes.
    """
    payload: dict[str, object] = {
        "objective": "conversions",
        "budget_type": "daily",
        "budget_amount": "50.00",
        "currency": "USD",
        "start_at": "2026-01-01T00:00:00Z",
        "end_at": "2026-02-01T00:00:00Z",
    }
    payload.update(overrides)
    return _create_campaign(client, token, **payload)


def _publish(client: TestClient, token: str, campaign_id: str):
    return client.post(
        f"{CAMPAIGNS_URL}/{campaign_id}/publish", headers=_auth_headers(token)
    )


def _list_deployments(client: TestClient, token: str, campaign_id: str):
    return client.get(
        f"{CAMPAIGNS_URL}/{campaign_id}/deployments", headers=_auth_headers(token)
    )


def _by_provider(items: list[dict], provider: str) -> dict:
    return next(item for item in items if item["provider"] == provider)


# --- Unauthenticated ---------------------------------------------------------


def test_publish_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(f"{CAMPAIGNS_URL}/{_NIL_UUID}/publish")
    assert response.status_code == 401


def test_list_deployments_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(f"{CAMPAIGNS_URL}/{_NIL_UUID}/deployments")
    assert response.status_code == 401


# --- Missing / cross-tenant campaign -----------------------------------------


def test_publish_missing_campaign_returns_404(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()

    response = _publish(client, token, _NIL_UUID)

    assert response.status_code == 404


def test_list_deployments_missing_campaign_returns_404(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()

    response = _list_deployments(client, token, _NIL_UUID)

    assert response.status_code == 404


def test_publish_cross_tenant_campaign_returns_404(
    client: TestClient, auth_fixture
) -> None:
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_campaign(client, token_a)

    response = _publish(client, token_b, created["id"])

    assert response.status_code == 404


def test_list_deployments_cross_tenant_returns_404(
    client: TestClient, auth_fixture
) -> None:
    """Another tenant cannot list a campaign's deployments, even after
    the owning tenant has published it.
    """
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_complete_campaign(client, token_a)
    _publish(client, token_a, created["id"])

    response = _list_deployments(client, token_b, created["id"])

    assert response.status_code == 404


# --- Publish against the real (still-unimplemented) adapter stubs -----------


def test_publish_creates_exactly_meta_and_google_deployments(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _publish(client, token, created["id"])

    assert response.status_code == 200
    items = response.json()["items"]
    providers = {item["provider"] for item in items}
    assert providers == {"meta", "google"}
    assert len(items) == 2


def test_publish_with_real_adapter_stubs_returns_failed_not_500(
    client: TestClient, auth_fixture
) -> None:
    """MetaAdapter/GoogleAdapter still raise NotImplementedError -- the
    endpoint must record that as a FAILED deployment and return 200,
    never let it escape as an unhandled 500. Both providers must be
    attempted (there are two failed items, one per provider), proving
    Meta's failure never prevented Google from being attempted.
    """
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _publish(client, token, created["id"])

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["status"] == "failed"
        assert item["last_error_message"] is not None
        assert item["external_campaign_id"] is None


def test_repeated_publish_does_not_duplicate_deployments(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    first = _publish(client, token, created["id"])
    second = _publish(client, token, created["id"])

    assert first.status_code == 200
    assert second.status_code == 200
    first_ids = {item["id"] for item in first.json()["items"]}
    second_ids = {item["id"] for item in second.json()["items"]}
    assert first_ids == second_ids

    listed = _list_deployments(client, token, created["id"])
    assert len(listed.json()["items"]) == 2


def test_existing_non_pending_deployment_is_reused_unchanged(
    client: TestClient, auth_fixture, db_session: Session
) -> None:
    """A deployment already advanced past pending (e.g. submitted by a
    prior successful publish) must come back unchanged from the
    publish endpoint, not be silently reset or re-attempted.
    """
    user, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    deployment_service = CampaignDeploymentService(
        CampaignDeploymentRepository(db_session), db_session
    )
    pending = deployment_service.create_pending_deployment(
        tenant_id=user.tenant_id,
        campaign_id=UUID(created["id"]),
        provider=CampaignDeploymentProvider.META,
    )
    submitted = deployment_service.mark_submitted(
        tenant_id=user.tenant_id,
        deployment_id=pending.id,
        external_campaign_id="pre-existing-ext-id",
    )

    response = _publish(client, token, created["id"])

    assert response.status_code == 200
    meta = _by_provider(response.json()["items"], "meta")
    assert meta["id"] == str(submitted.id)
    assert meta["status"] == "submitted"
    assert meta["external_campaign_id"] == "pre-existing-ext-id"


# --- Deployment listing -------------------------------------------------------


def test_list_deployments_returns_deterministic_provider_order(
    client: TestClient, auth_fixture
) -> None:
    """The repository orders by created_at then id (see
    CampaignDeploymentRepository.list_by_campaign) -- Meta and Google
    are created in the same publish_campaign() call and can share an
    identical created_at, so the id tie-breaker (not insertion order)
    decides which comes first. The guarantee under test is therefore
    that repeated listings return the exact same order, not that Meta
    always precedes Google.
    """
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)
    _publish(client, token, created["id"])

    first = _list_deployments(client, token, created["id"])
    second = _list_deployments(client, token, created["id"])

    assert first.status_code == 200
    assert second.status_code == 200
    providers_first = [item["provider"] for item in first.json()["items"]]
    providers_second = [item["provider"] for item in second.json()["items"]]
    assert set(providers_first) == {"meta", "google"}
    assert providers_first == providers_second


def test_list_deployments_before_publish_returns_empty_items(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _list_deployments(client, token, created["id"])

    assert response.status_code == 200
    assert response.json()["items"] == []


# --- Response schema ----------------------------------------------------------


def test_publish_response_schema_has_expected_fields_and_no_idempotency_key(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _publish(client, token, created["id"])

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    expected_keys = {
        "id",
        "campaign_id",
        "provider",
        "status",
        "external_campaign_id",
        "submitted_at",
        "confirmed_at",
        "last_error_message",
        "created_at",
        "updated_at",
    }
    for item in items:
        assert set(item.keys()) == expected_keys
        assert "idempotency_key" not in item
        assert item["campaign_id"] == created["id"]
        assert item["provider"] in {"meta", "google"}


# --- Fake-adapter success path (focused HTTP-level test) --------------------


class _FakeSuccessAdapter(BaseAdapter):
    """Always reports a successful publish with a fixed external ID.

    A plain BaseAdapter subclass, exactly like the fakes in
    test_publish_campaign_adapter_integration.py -- not a mock, not a
    monkeypatch of MetaAdapter/GoogleAdapter.
    """

    def __init__(self, external_campaign_id: str) -> None:
        self._external_campaign_id = external_campaign_id

    def publish(self, spec: CampaignSpec) -> PublishResult:
        return PublishResult(
            success=True,
            external_campaign_id=self._external_campaign_id,
            error_message=None,
        )

    def pause(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by this test.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by this test.")


class _FakeAdapterRegistry:
    """Duck-typed stand-in for ProviderAdapterRegistry (get(provider) ->
    BaseAdapter), injected only via app.dependency_overrides -- the real
    ProviderAdapterRegistry class is never modified or subclassed.
    """

    def __init__(self, adapters: dict[CampaignDeploymentProvider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: CampaignDeploymentProvider) -> BaseAdapter:
        return self._adapters[provider]


@pytest.fixture
def fake_success_registry() -> _FakeAdapterRegistry:
    return _FakeAdapterRegistry(
        {
            CampaignDeploymentProvider.META: _FakeSuccessAdapter("meta-ext-http-1"),
            CampaignDeploymentProvider.GOOGLE: _FakeSuccessAdapter("google-ext-http-1"),
        }
    )


def test_publish_with_fake_success_adapter_returns_submitted_status(
    client: TestClient, auth_fixture, fake_success_registry: _FakeAdapterRegistry
) -> None:
    """Focused test verifying the HTTP success-path response shape end
    to end. Only the get_provider_adapter_registry dependency is
    overridden for the duration of this test -- authentication, the
    database session, and every other dependency run unmodified.
    """
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    app.dependency_overrides[get_provider_adapter_registry] = (
        lambda: fake_success_registry
    )
    try:
        response = _publish(client, token, created["id"])
    finally:
        app.dependency_overrides.pop(get_provider_adapter_registry, None)

    assert response.status_code == 200
    items = response.json()["items"]
    meta = _by_provider(items, "meta")
    google = _by_provider(items, "google")
    assert meta["status"] == "submitted"
    assert meta["external_campaign_id"] == "meta-ext-http-1"
    assert google["status"] == "submitted"
    assert google["external_campaign_id"] == "google-ext-http-1"

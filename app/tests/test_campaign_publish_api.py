"""API tests for async publish enqueue + job status + deployment listing.

HTTP wiring only: PublishJobService.enqueue / get_job and
PublishCampaignService.list_deployments. Domain logic is covered
elsewhere. Authentication only (no local RBAC).

Enqueue does not run adapters in-request. Tests that need deployments
after enqueue drive PublishJobService.run_once() on the test session.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.base_adapter import BaseAdapter
from app.adapters.models import PublishResult
from app.adapters.registry import ProviderAdapterRegistry
from app.api.dependencies import get_credential_encryption_service
from app.core.campaign_spec import CampaignSpec
from app.core.provider_credentials import ProviderCredentials
from app.core.providers import Provider
from app.core.security.credential_encryption import CredentialEncryptionService
from app.main import app
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
from app.services.publish_job_service import PublishJobService

_TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")

CAMPAIGNS_URL = "/api/v1/campaigns"
_NIL_UUID = "00000000-0000-0000-0000-000000000000"
_JOB_EXPECTED_KEYS = {
    "id",
    "tenant_id",
    "campaign_id",
    "requested_by_user_id",
    "status",
    "attempt_count",
    "error_message",
    "started_at",
    "finished_at",
    "created_at",
    "updated_at",
}


@pytest.fixture
def encryption_service() -> CredentialEncryptionService:
    return CredentialEncryptionService(_TEST_ENCRYPTION_KEY)


@pytest.fixture(autouse=True)
def _override_encryption_service(
    encryption_service: CredentialEncryptionService,
) -> Iterator[None]:
    """Override so API tests do not require ENCRYPTION_KEY in .env."""
    app.dependency_overrides[get_credential_encryption_service] = (
        lambda: encryption_service
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_credential_encryption_service, None)


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


def _get_job(client: TestClient, token: str, campaign_id: str, job_id: str):
    return client.get(
        f"{CAMPAIGNS_URL}/{campaign_id}/publish-jobs/{job_id}",
        headers=_auth_headers(token),
    )


def _list_deployments(client: TestClient, token: str, campaign_id: str):
    return client.get(
        f"{CAMPAIGNS_URL}/{campaign_id}/deployments", headers=_auth_headers(token)
    )


def _by_provider(items: list[dict], provider: str) -> dict:
    return next(item for item in items if item["provider"] == provider)


def _process_one_job(
    db_session: Session,
    encryption_service: CredentialEncryptionService,
    *,
    adapter_registry: object | None = None,
) -> bool:
    """Drive one worker-equivalent iteration on the test session."""
    deployment_repository = CampaignDeploymentRepository(db_session)
    campaign_repository = CampaignRepository(db_session)
    connection_repository = ProviderConnectionRepository(db_session)
    job_repository = PublishJobRepository(db_session)
    deployment_service = CampaignDeploymentService(deployment_repository, db_session)
    connection_service = ProviderConnectionService(
        connection_repository, encryption_service, db_session
    )
    publish_campaign_service = PublishCampaignService(
        campaign_repository,
        deployment_repository,
        deployment_service,
        CampaignSpecBuilder(),
        adapter_registry or ProviderAdapterRegistry(),
        connection_service,
        db_session,
    )
    job_service = PublishJobService(
        job_repository,
        campaign_repository,
        publish_campaign_service,
        db_session,
    )
    return job_service.run_once()


# --- Unauthenticated ---------------------------------------------------------


def test_publish_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(f"{CAMPAIGNS_URL}/{_NIL_UUID}/publish")
    assert response.status_code == 401


def test_get_publish_job_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(f"{CAMPAIGNS_URL}/{_NIL_UUID}/publish-jobs/{_NIL_UUID}")
    assert response.status_code == 401


def test_list_deployments_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(f"{CAMPAIGNS_URL}/{_NIL_UUID}/deployments")
    assert response.status_code == 401


# --- Missing / cross-tenant / archived ---------------------------------------


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
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_complete_campaign(client, token_a)
    _publish(client, token_a, created["id"])

    response = _list_deployments(client, token_b, created["id"])

    assert response.status_code == 404


def test_publish_archived_campaign_returns_409(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)
    archive = client.post(
        f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token)
    )
    assert archive.status_code == 200

    response = _publish(client, token, created["id"])

    assert response.status_code == 409


# --- Enqueue -----------------------------------------------------------------


def test_publish_enqueues_queued_job_with_202(
    client: TestClient, auth_fixture
) -> None:
    user, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _publish(client, token, created["id"])

    assert response.status_code == 202
    body = response.json()
    assert set(body.keys()) == {"job"}
    assert "items" not in body
    job = body["job"]
    assert set(job.keys()) == _JOB_EXPECTED_KEYS
    assert job["status"] == "queued"
    assert job["campaign_id"] == created["id"]
    assert job["tenant_id"] == str(user.tenant_id)
    assert job["requested_by_user_id"] == str(user.id)
    assert job["attempt_count"] == 0
    assert job["error_message"] is None


def test_publish_does_not_create_deployments_in_request(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    publish = _publish(client, token, created["id"])
    assert publish.status_code == 202

    listed = _list_deployments(client, token, created["id"])
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_repeated_publish_returns_same_active_job(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    first = _publish(client, token, created["id"])
    second = _publish(client, token, created["id"])

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    assert second.json()["job"]["status"] == "queued"


def test_get_publish_job_happy(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)
    enqueued = _publish(client, token, created["id"])
    job_id = enqueued.json()["job"]["id"]

    response = _get_job(client, token, created["id"], job_id)

    assert response.status_code == 200
    assert response.json()["job"]["id"] == job_id
    assert response.json()["job"]["status"] == "queued"


def test_get_publish_job_wrong_tenant_returns_404(
    client: TestClient, auth_fixture
) -> None:
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_complete_campaign(client, token_a)
    job_id = _publish(client, token_a, created["id"]).json()["job"]["id"]

    response = _get_job(client, token_b, created["id"], job_id)

    assert response.status_code == 404


def test_get_publish_job_wrong_campaign_returns_404(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    campaign_a = _create_complete_campaign(client, token)
    campaign_b = _create_complete_campaign(client, token, name="Other campaign")
    job_id = _publish(client, token, campaign_a["id"]).json()["job"]["id"]

    response = _get_job(client, token, campaign_b["id"], job_id)

    assert response.status_code == 404


def test_get_publish_job_missing_returns_404(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)

    response = _get_job(client, token, created["id"], str(uuid4()))

    assert response.status_code == 404


# --- Deployments after worker processes the job ------------------------------


def test_worker_run_creates_failed_deployments_for_stub_adapters(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)
    assert _publish(client, token, created["id"]).status_code == 202

    assert _process_one_job(db_session, encryption_service) is True

    listed = _list_deployments(client, token, created["id"])
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 2
    assert {item["provider"] for item in items} == {"meta", "google"}
    for item in items:
        assert item["status"] == "failed"
        assert item["last_error_message"] is not None


def test_list_deployments_returns_deterministic_provider_order(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    _, token = auth_fixture()
    created = _create_complete_campaign(client, token)
    _publish(client, token, created["id"])
    _process_one_job(db_session, encryption_service)

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


# --- Fake-adapter success path via worker ------------------------------------


class _FakeSuccessAdapter(BaseAdapter):
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
        raise NotImplementedError("Not exercised by this test.")

    def resume(self, external_campaign_id: str) -> None:
        raise NotImplementedError("Not exercised by this test.")


class _FakeAdapterRegistry:
    def __init__(self, adapters: dict[Provider, BaseAdapter]) -> None:
        self._adapters = adapters

    def get(self, provider: Provider) -> BaseAdapter:
        return self._adapters[provider]


def test_worker_with_fake_success_adapter_marks_deployments_submitted(
    client: TestClient,
    auth_fixture,
    db_session: Session,
    encryption_service: CredentialEncryptionService,
) -> None:
    user, token = auth_fixture()
    created = _create_complete_campaign(client, token)
    connection_service = ProviderConnectionService(
        ProviderConnectionRepository(db_session),
        encryption_service,
        db_session,
    )
    for provider in (Provider.META, Provider.GOOGLE):
        connection_service.connect(
            tenant_id=user.tenant_id,
            provider=provider,
            credential_payload=b"opaque-http-success-credential",
        )

    enqueued = _publish(client, token, created["id"])
    assert enqueued.status_code == 202
    job_id = enqueued.json()["job"]["id"]

    registry = _FakeAdapterRegistry(
        {
            Provider.META: _FakeSuccessAdapter("meta-ext-http-1"),
            Provider.GOOGLE: _FakeSuccessAdapter("google-ext-http-1"),
        }
    )
    assert (
        _process_one_job(
            db_session, encryption_service, adapter_registry=registry
        )
        is True
    )

    job = _get_job(client, token, created["id"], job_id)
    assert job.status_code == 200
    assert job.json()["job"]["status"] == "succeeded"

    listed = _list_deployments(client, token, created["id"])
    items = listed.json()["items"]
    meta = _by_provider(items, "meta")
    google = _by_provider(items, "google")
    assert meta["status"] == "submitted"
    assert meta["external_campaign_id"] == "meta-ext-http-1"
    assert google["status"] == "submitted"
    assert google["external_campaign_id"] == "google-ext-http-1"
    assert "idempotency_key" not in meta

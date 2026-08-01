"""API tests for /api/v1/campaigns (Campaign Management Milestone 1).

MILESTONE 1 SCOPE: draft creation/listing/retrieval/editing and
draft-to-archived only -- no ready/publish transition, no provider
integration. Every route enforces authentication only (no local RBAC) --
see app/api/v1/campaigns.py and docs/HANDOFF.md.
"""

from fastapi.testclient import TestClient

CAMPAIGNS_URL = "/api/v1/campaigns"
_NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_campaign(client: TestClient, token: str, **overrides: object) -> dict:
    payload: dict[str, object] = {"name": "Test Campaign"}
    payload.update(overrides)
    response = client.post(CAMPAIGNS_URL, json=payload, headers=_auth_headers(token))
    assert response.status_code == 201, response.text
    return response.json()


# --- Unauthenticated ---


def test_create_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(CAMPAIGNS_URL, json={"name": "X"})
    assert response.status_code == 401


def test_list_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(CAMPAIGNS_URL)
    assert response.status_code == 401


def test_get_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.get(f"{CAMPAIGNS_URL}/{_NIL_UUID}")
    assert response.status_code == 401


def test_update_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.patch(f"{CAMPAIGNS_URL}/{_NIL_UUID}", json={})
    assert response.status_code == 401


def test_archive_unauthenticated_returns_401(client: TestClient) -> None:
    response = client.post(f"{CAMPAIGNS_URL}/{_NIL_UUID}/archive")
    assert response.status_code == 401


def test_list_soft_deleted_user_is_denied(client: TestClient, auth_fixture) -> None:
    """A soft-deleted user's token is rejected at the authentication layer,
    before this router's own logic ever runs.
    """
    _, token = auth_fixture(user_deleted=True)

    response = client.get(CAMPAIGNS_URL, headers=_auth_headers(token))

    assert response.status_code == 401


# --- Happy paths ---


def test_create_campaign_succeeds(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()

    body = _create_campaign(client, token, name="My Campaign")

    assert body["name"] == "My Campaign"
    assert body["status"] == "draft"
    assert body["objective"] is None
    assert body["budget_amount"] is None


def test_list_campaigns_returns_created_campaign(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)

    response = client.get(CAMPAIGNS_URL, headers=_auth_headers(token))

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert created["id"] in ids


def test_get_campaign_succeeds(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)

    response = client.get(f"{CAMPAIGNS_URL}/{created['id']}", headers=_auth_headers(token))

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_update_draft_campaign_succeeds(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token, name="Before")

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}",
        json={"name": "After"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "After"


def test_archive_draft_campaign_succeeds(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)

    response = client.post(
        f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "archived"


# --- Tenant isolation ---


def test_get_other_tenant_campaign_returns_404(client: TestClient, auth_fixture) -> None:
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_campaign(client, token_a)

    response = client.get(f"{CAMPAIGNS_URL}/{created['id']}", headers=_auth_headers(token_b))

    assert response.status_code == 404


def test_update_other_tenant_campaign_returns_404(client: TestClient, auth_fixture) -> None:
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_campaign(client, token_a)

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}",
        json={"name": "Hijacked"},
        headers=_auth_headers(token_b),
    )

    assert response.status_code == 404


def test_archive_other_tenant_campaign_returns_404(client: TestClient, auth_fixture) -> None:
    _, token_a = auth_fixture()
    _, token_b = auth_fixture()
    created = _create_campaign(client, token_a)

    response = client.post(
        f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token_b)
    )

    assert response.status_code == 404


# --- Validation ---


def test_invalid_partial_budget_update_returns_422(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}",
        json={"budget_amount": "10.00"},  # currency/budget_type still unset
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_unknown_request_field_returns_422(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL,
        json={"name": "X", "unexpected_field": "value"},
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_tenant_id_cannot_be_supplied_by_client(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL,
        json={"name": "X", "tenant_id": _NIL_UUID},
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_status_cannot_be_supplied_by_client(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL, json={"name": "X", "status": "active"}, headers=_auth_headers(token)
    )

    assert response.status_code == 422


def test_created_by_user_id_cannot_be_supplied_by_client(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL,
        json={"name": "X", "created_by_user_id": _NIL_UUID},
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


# --- Non-draft state conflicts ---


def test_update_archived_campaign_returns_409(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)
    client.post(f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token))

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}", json={"name": "Y"}, headers=_auth_headers(token)
    )

    assert response.status_code == 409


def test_archive_archived_campaign_returns_409(client: TestClient, auth_fixture) -> None:
    _, token = auth_fixture()
    created = _create_campaign(client, token)
    client.post(f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token))

    response = client.post(
        f"{CAMPAIGNS_URL}/{created['id']}/archive", headers=_auth_headers(token)
    )

    assert response.status_code == 409


def test_status_filter_returns_only_matching_campaigns(
    client: TestClient, auth_fixture
) -> None:
    _, token = auth_fixture()
    draft = _create_campaign(client, token, name="Draft one")
    to_archive = _create_campaign(client, token, name="Will archive")
    client.post(f"{CAMPAIGNS_URL}/{to_archive['id']}/archive", headers=_auth_headers(token))

    response = client.get(f"{CAMPAIGNS_URL}?status=draft", headers=_auth_headers(token))

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert draft["id"] in ids
    assert to_archive["id"] not in ids


# --- Additional edge cases ---


def test_budget_round_trip_preserves_exact_value(client: TestClient, auth_fixture) -> None:
    """A fully-specified budget is returned unchanged by both the create
    response and a subsequent fetch -- guards against silent precision
    loss or type drift (e.g. Decimal serialized/round-tripped as float).
    """
    _, token = auth_fixture()
    created = _create_campaign(
        client,
        token,
        budget_type="daily",
        budget_amount="1234.56",
        currency="USD",
    )
    assert created["budget_amount"] == "1234.56"
    assert created["currency"] == "USD"

    fetched = client.get(f"{CAMPAIGNS_URL}/{created['id']}", headers=_auth_headers(token))
    assert fetched.json()["budget_amount"] == "1234.56"
    assert fetched.json()["currency"] == "USD"


def test_invalid_partial_budget_create_returns_422(client: TestClient, auth_fixture) -> None:
    """The budget all-or-none rule is enforced at creation time too, not
    only on PATCH.
    """
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL,
        json={"name": "X", "currency": "USD"},  # budget_type/amount missing
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_create_rejects_budget_amount_with_excess_decimal_precision(
    client: TestClient, auth_fixture
) -> None:
    """PostgreSQL's NUMERIC(12, 2) would otherwise silently round this to
    11.00 with no error, altering the caller's requested spend.
    """
    _, token = auth_fixture()

    response = client.post(
        CAMPAIGNS_URL,
        json={
            "name": "X",
            "budget_type": "daily",
            "budget_amount": "10.995",
            "currency": "USD",
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_update_rejects_naive_datetime_mixed_with_aware_returns_422(
    client: TestClient, auth_fixture
) -> None:
    """A naive datetime compared against an aware one previously raised an
    unhandled TypeError (500) instead of a clean validation error.
    """
    _, token = auth_fixture()
    created = _create_campaign(
        client, token, start_at="2026-01-01T00:00:00Z"
    )

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}",
        json={"end_at": "2026-01-02T00:00:00"},  # no timezone offset
        headers=_auth_headers(token),
    )

    assert response.status_code == 422


def test_empty_patch_body_is_a_noop(client: TestClient, auth_fixture) -> None:
    """A PATCH with no fields set (exclude_unset yields {}) succeeds and
    leaves the campaign unchanged, rather than being rejected outright.
    """
    _, token = auth_fixture()
    created = _create_campaign(client, token, name="Unchanged")

    response = client.patch(
        f"{CAMPAIGNS_URL}/{created['id']}", json={}, headers=_auth_headers(token)
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Unchanged"


def test_list_pagination_does_not_skip_or_repeat_rows_across_pages(
    client: TestClient, auth_fixture
) -> None:
    """Regression test: campaigns created within the same request/test can
    share an identical created_at, which previously made LIMIT/OFFSET
    pagination order (and therefore page contents) non-deterministic.
    """
    _, token = auth_fixture()
    first = _create_campaign(client, token, name="First")
    second = _create_campaign(client, token, name="Second")

    page_1 = client.get(f"{CAMPAIGNS_URL}?limit=1&offset=0", headers=_auth_headers(token))
    page_2 = client.get(f"{CAMPAIGNS_URL}?limit=1&offset=1", headers=_auth_headers(token))

    id_1 = page_1.json()["items"][0]["id"]
    id_2 = page_2.json()["items"][0]["id"]
    assert {id_1, id_2} == {first["id"], second["id"]}
    assert id_1 != id_2

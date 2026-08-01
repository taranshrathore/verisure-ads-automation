"""Pure unit tests for the adapter abstraction layer (Milestone 4).

No database, no network, no OAuth, no HTTP: BaseAdapter, MetaAdapter,
GoogleAdapter, and ProviderAdapterRegistry are all plain in-memory
Python objects with no I/O dependencies.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.adapters.base_adapter import BaseAdapter
from app.adapters.google_adapter import GoogleAdapter
from app.adapters.meta_adapter import MetaAdapter
from app.adapters.models import PublishResult
from app.adapters.registry import ProviderAdapterRegistry
from app.core.campaign_spec import CampaignBudget, CampaignSchedule, CampaignSpec
from app.models.campaign import CampaignBudgetType, CampaignObjective
from app.models.campaign_deployment import CampaignDeploymentProvider


def _make_spec(
    provider: CampaignDeploymentProvider = CampaignDeploymentProvider.META,
) -> CampaignSpec:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return CampaignSpec(
        campaign_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        provider=provider,
        objective=CampaignObjective.CONVERSIONS,
        budget=CampaignBudget(
            type=CampaignBudgetType.DAILY, amount=Decimal("50.00"), currency="USD"
        ),
        schedule=CampaignSchedule(
            start_at=now, end_at=now + timedelta(days=30)
        ),
    )


# --- Registry ----------------------------------------------------------------


def test_registry_returns_meta_adapter_for_meta_provider() -> None:
    registry = ProviderAdapterRegistry()

    adapter = registry.get(CampaignDeploymentProvider.META)

    assert isinstance(adapter, MetaAdapter)


def test_registry_returns_google_adapter_for_google_provider() -> None:
    registry = ProviderAdapterRegistry()

    adapter = registry.get(CampaignDeploymentProvider.GOOGLE)

    assert isinstance(adapter, GoogleAdapter)


def test_registry_raises_value_error_for_unknown_provider() -> None:
    registry = ProviderAdapterRegistry()

    with pytest.raises(ValueError):
        registry.get("tiktok")  # type: ignore[arg-type]


# --- Inheritance ---------------------------------------------------------------


def test_meta_adapter_inherits_base_adapter() -> None:
    assert isinstance(MetaAdapter(), BaseAdapter)


def test_google_adapter_inherits_base_adapter() -> None:
    assert isinstance(GoogleAdapter(), BaseAdapter)


def test_base_adapter_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        BaseAdapter()  # type: ignore[abstract]


# --- Unimplemented behavior ----------------------------------------------------


def test_meta_adapter_publish_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        MetaAdapter().publish(_make_spec(CampaignDeploymentProvider.META))


def test_meta_adapter_pause_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        MetaAdapter().pause("ext-123")


def test_meta_adapter_resume_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        MetaAdapter().resume("ext-123")


def test_google_adapter_publish_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        GoogleAdapter().publish(_make_spec(CampaignDeploymentProvider.GOOGLE))


def test_google_adapter_pause_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        GoogleAdapter().pause("ext-456")


def test_google_adapter_resume_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        GoogleAdapter().resume("ext-456")


# --- PublishResult invariants (Milestone 5 audit) -------------------------------


def test_publish_result_accepts_valid_success() -> None:
    result = PublishResult(
        success=True, external_campaign_id="ext-123", error_message=None
    )
    assert result.success is True
    assert result.external_campaign_id == "ext-123"


def test_publish_result_accepts_valid_failure() -> None:
    result = PublishResult(
        success=False, external_campaign_id=None, error_message="declined"
    )
    assert result.success is False
    assert result.error_message == "declined"


def test_publish_result_rejects_success_with_none_external_campaign_id() -> None:
    with pytest.raises(ValueError):
        PublishResult(success=True, external_campaign_id=None, error_message=None)


def test_publish_result_rejects_success_with_blank_external_campaign_id() -> None:
    with pytest.raises(ValueError):
        PublishResult(success=True, external_campaign_id="   ", error_message=None)


def test_publish_result_rejects_failure_with_none_error_message() -> None:
    with pytest.raises(ValueError):
        PublishResult(success=False, external_campaign_id=None, error_message=None)


def test_publish_result_rejects_failure_with_blank_error_message() -> None:
    with pytest.raises(ValueError):
        PublishResult(success=False, external_campaign_id=None, error_message="   ")


def test_publish_result_rejects_success_with_error_message_populated() -> None:
    with pytest.raises(ValueError):
        PublishResult(
            success=True, external_campaign_id="ext-123", error_message="oops"
        )


def test_publish_result_rejects_failure_with_external_campaign_id_populated() -> None:
    with pytest.raises(ValueError):
        PublishResult(
            success=False, external_campaign_id="ext-123", error_message="declined"
        )

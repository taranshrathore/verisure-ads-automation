"""Campaign management service: draft CRUD and archiving.

CampaignService owns all transaction commits; CampaignRepository never
commits. Every method takes tenant_id explicitly and never trusts a
value taken from a request body -- callers (the API layer) must derive
tenant_id and created_by_user_id exclusively from the authenticated
user's token.

MILESTONE 1 SCOPE: only draft creation/editing and draft-to-archived are
implemented. There is no "ready" transition and no publish path yet --
see app/models/campaign.py for why the full CampaignStatus enum already
exists despite this.
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignNotFoundError,
    CampaignValidationError,
    InvalidCampaignStateError,
)
from app.models.campaign import Campaign, CampaignBudgetType, CampaignObjective, CampaignStatus
from app.repositories.campaign_repository import CampaignRepository

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")

# Numeric(12, 2): 12 total digits, 2 after the decimal point -> the largest
# representable magnitude is 10**10 - 0.01. Values outside this range, or
# with more than 2 decimal places, are silently rounded/rejected by
# PostgreSQL with no application-visible error unless rejected here first.
_MAX_BUDGET_AMOUNT = Decimal("9999999999.99")

# Fields a caller may set at creation or via a partial update. Kept as a
# single source of truth so update_draft can both reject unknown keys and
# know exactly which columns to merge/write.
_MUTABLE_FIELDS = frozenset(
    {
        "name",
        "objective",
        "budget_type",
        "budget_amount",
        "currency",
        "start_at",
        "end_at",
    }
)


def _validate_campaign_fields(
    *,
    budget_type: CampaignBudgetType | None,
    budget_amount: Decimal | None,
    currency: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
) -> None:
    """Mirror the database CHECK constraints in Python, before any write.

    This lets invalid input fail as a clean 422 (CampaignValidationError)
    instead of an unhandled IntegrityError surfacing as a generic 500.
    """
    budget_fields = (budget_type, budget_amount, currency)
    any_set = any(field is not None for field in budget_fields)
    all_set = all(field is not None for field in budget_fields)
    if any_set and not all_set:
        raise CampaignValidationError(
            "budget_type, budget_amount, and currency must be all set "
            "together or all omitted."
        )

    if budget_amount is not None and budget_amount <= 0:
        raise CampaignValidationError("budget_amount must be positive.")

    if budget_amount is not None and budget_amount.as_tuple().exponent < -2:
        # PostgreSQL's NUMERIC(12, 2) rounds excess precision (e.g. 10.995 ->
        # 11.00) rather than erroring, which would silently alter a client's
        # requested ad spend. Reject it here instead so the caller gets a
        # 422 explaining exactly what happened.
        raise CampaignValidationError(
            "budget_amount must have at most two decimal places."
        )

    if budget_amount is not None and abs(budget_amount) > _MAX_BUDGET_AMOUNT:
        raise CampaignValidationError(
            f"budget_amount must not exceed {_MAX_BUDGET_AMOUNT}."
        )

    if currency is not None and not _CURRENCY_PATTERN.fullmatch(currency):
        raise CampaignValidationError(
            "currency must be exactly three uppercase ASCII letters."
        )

    # A naive datetime compared against a timezone-aware one raises
    # TypeError, not a domain error -- that would surface as an unhandled
    # 500 instead of a 422. Schedule fields are also genuinely ambiguous
    # without an explicit timezone, so both are rejected explicitly here
    # before any comparison is attempted.
    if start_at is not None and start_at.tzinfo is None:
        raise CampaignValidationError("start_at must include timezone information.")

    if end_at is not None and end_at.tzinfo is None:
        raise CampaignValidationError("end_at must include timezone information.")

    if start_at is not None and end_at is not None and end_at <= start_at:
        raise CampaignValidationError("end_at must be after start_at.")


class CampaignService:
    """Orchestrates campaign use cases across the CampaignRepository."""

    def __init__(
        self, campaign_repository: CampaignRepository, session: Session
    ) -> None:
        self._campaigns = campaign_repository
        self._session = session

    def create_draft(
        self,
        *,
        tenant_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        name: str,
        objective: CampaignObjective | None = None,
        budget_type: CampaignBudgetType | None = None,
        budget_amount: Decimal | None = None,
        currency: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> Campaign:
        """Create a new draft campaign for the caller's own tenant.

        Only name (plus tenant/creator/status) is required; a draft may
        be otherwise incomplete. Any budget/schedule fields supplied are
        still validated together for internal consistency.
        """
        _validate_campaign_fields(
            budget_type=budget_type,
            budget_amount=budget_amount,
            currency=currency,
            start_at=start_at,
            end_at=end_at,
        )

        campaign = Campaign(
            tenant_id=tenant_id,
            created_by_user_id=created_by_user_id,
            name=name,
            objective=objective,
            budget_type=budget_type,
            budget_amount=budget_amount,
            currency=currency,
            start_at=start_at,
            end_at=end_at,
            status=CampaignStatus.DRAFT,
        )
        self._campaigns.create(campaign)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        return campaign

    def get_campaign(self, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
        """Return a single tenant-scoped campaign, or raise if not found."""
        campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError()
        return campaign

    def list_campaigns(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
        status: CampaignStatus | None = None,
    ) -> list[Campaign]:
        """Return a paginated, tenant-scoped list of campaigns.

        Archived campaigns are included by default -- archiving is a
        terminal business state, not a deletion.
        """
        return self._campaigns.list_by_tenant(
            tenant_id, limit=limit, offset=offset, status=status
        )

    def update_draft(
        self,
        *,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        updates: dict[str, Any],
    ) -> Campaign:
        """Apply a partial update to a draft campaign.

        `updates` must contain only keys the caller explicitly set (e.g.
        via Pydantic's model_dump(exclude_unset=True)), so that an
        omitted field is left untouched while an explicitly-null field
        clears it. The merged result (existing values overridden only by
        explicitly-provided keys) is validated as a whole before any
        write is attempted, and the write itself is a single conditional
        UPDATE re-checking status == draft to close the read/write race.
        """
        unknown_fields = set(updates) - _MUTABLE_FIELDS
        if unknown_fields:
            raise CampaignValidationError(
                f"Unsupported field(s): {', '.join(sorted(unknown_fields))}."
            )

        campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError()

        merged = {
            field: updates[field] if field in updates else getattr(campaign, field)
            for field in _MUTABLE_FIELDS
        }
        _validate_campaign_fields(
            budget_type=merged["budget_type"],
            budget_amount=merged["budget_amount"],
            currency=merged["currency"],
            start_at=merged["start_at"],
            end_at=merged["end_at"],
        )

        affected = self._campaigns.update_draft_fields(tenant_id, campaign_id, merged)
        if affected == 0:
            raise InvalidCampaignStateError(
                "Only a draft campaign can be edited."
            )

        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        self._session.refresh(campaign)
        return campaign

    def archive_campaign(
        self, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID
    ) -> Campaign:
        """Transition a draft campaign to archived.

        Archiving never sets deleted_at: archived campaigns remain fully
        retrievable and appear in default list results.
        """
        campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError()

        affected = self._campaigns.archive_draft(tenant_id, campaign_id)
        if affected == 0:
            raise InvalidCampaignStateError("Only a draft campaign can be archived.")

        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        self._session.refresh(campaign)
        return campaign

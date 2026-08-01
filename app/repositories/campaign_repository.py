"""Campaign persistence repository."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignStatus


class CampaignRepository:
    """Data-access helpers for Campaign rows. Does not commit or roll back.

    Every method is tenant-scoped: there is no method here that can look
    up or mutate a campaign without a tenant_id predicate. Ordinary reads
    filter deleted_at IS NULL (true soft-deletion is reserved, unused
    today -- archived campaigns are NOT soft-deleted and remain visible).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, campaign: Campaign) -> None:
        """Stage a new campaign for persistence."""
        self._session.add(campaign)

    def get_by_tenant_and_id(
        self, tenant_id: UUID, campaign_id: UUID
    ) -> Campaign | None:
        """Return a tenant-scoped, non-deleted campaign by ID, or None."""
        return self._session.scalar(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
                Campaign.deleted_at.is_(None),
            )
        )

    def list_by_tenant(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
        status: CampaignStatus | None = None,
    ) -> list[Campaign]:
        """Return a paginated, tenant-scoped list of non-deleted campaigns.

        Archived campaigns are included by default (they are not
        soft-deleted); pass status to filter to exactly one lifecycle
        state.
        """
        stmt = select(Campaign).where(
            Campaign.tenant_id == tenant_id, Campaign.deleted_at.is_(None)
        )
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        # created_at alone is not a unique sort key: two campaigns can share
        # the same value (e.g. created in the same transaction, or simply
        # the same timestamp-precision tick under load). Without a
        # tie-breaker, LIMIT/OFFSET pagination over ties is undefined order
        # and can skip or repeat rows across pages. id is unique, so this
        # guarantees a total order.
        stmt = (
            stmt.order_by(Campaign.created_at.desc(), Campaign.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt))

    def update_draft_fields(
        self, tenant_id: UUID, campaign_id: UUID, values: dict[str, object]
    ) -> int:
        """Conditionally update a draft campaign's fields.

        The WHERE predicate re-checks status == draft at write time (not
        merely at an earlier read time), closing the race where a
        concurrent archive lands between the caller's read and this
        write. Returns the affected row count so the caller can detect a
        lost race (0 rows) versus success (1 row) without a second query.
        """
        result = self._session.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
                Campaign.deleted_at.is_(None),
                Campaign.status == CampaignStatus.DRAFT,
            )
            .values(**values)
        )
        return result.rowcount

    def archive_draft(self, tenant_id: UUID, campaign_id: UUID) -> int:
        """Conditionally transition a draft campaign to archived.

        Same conditional-UPDATE-with-rowcount pattern as
        update_draft_fields, for the same race-safety reason. Does not
        touch deleted_at.
        """
        result = self._session.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
                Campaign.deleted_at.is_(None),
                Campaign.status == CampaignStatus.DRAFT,
            )
            .values(status=CampaignStatus.ARCHIVED)
        )
        return result.rowcount

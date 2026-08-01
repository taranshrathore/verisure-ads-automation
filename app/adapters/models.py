"""Pure result dataclasses for provider adapters.

No ORM, no SQLAlchemy, no Pydantic, no I/O -- mirrors the same
"provider-neutral, framework-free" principle as app/core/campaign_spec.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PublishResult:
    """Outcome of a single BaseAdapter.publish attempt.

    The success/external_campaign_id/error_message invariant is
    enforced here, at construction, rather than left to callers to
    honor by convention: a PublishResult that violates it can never
    exist, so PublishCampaignService (and any future adapter) can rely
    on success=True always meaning "there is a real external ID and no
    error", and success=False always meaning "there is a real error
    and no external ID", without re-checking it themselves. An adapter
    bug that would otherwise construct an invalid combination instead
    raises ValueError immediately, from inside adapter.publish -- which
    is still just an ordinary Exception to PublishCampaignService's
    per-provider exception handling, so it is recorded as that
    provider's failure rather than crashing the whole publish.
    """

    success: bool
    external_campaign_id: str | None
    error_message: str | None

    def __post_init__(self) -> None:
        if self.success:
            if not self.external_campaign_id or not self.external_campaign_id.strip():
                raise ValueError(
                    "A successful PublishResult must include a non-empty "
                    "external_campaign_id."
                )
            if self.error_message is not None:
                raise ValueError(
                    "A successful PublishResult must not include an error_message."
                )
        else:
            if not self.error_message or not self.error_message.strip():
                raise ValueError(
                    "A failed PublishResult must include a non-empty error_message."
                )
            if self.external_campaign_id is not None:
                raise ValueError(
                    "A failed PublishResult must not include an external_campaign_id."
                )

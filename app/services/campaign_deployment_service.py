"""CampaignDeployment lifecycle service.

CampaignDeploymentService owns transaction commits for standalone
lifecycle calls (commit=True, the default). When participating in an
outer unit of work -- notably PublishJobService.run_once -- callers pass
commit=False so this service only flushes and never commits or rolls
back; the outer owner performs the single commit/rollback.

CampaignDeploymentRepository never commits. Every method takes tenant_id
explicitly and is tenant-scoped throughout.

Every public method that writes wraps its full read-validate-write(s)
sequence in a single try/except. With commit=True it rolls back on ANY
exception before re-raising. With commit=False it re-raises without
rolling back so the outer unit of work retains ownership.

MILESTONE 2 PHASE 3 SCOPE: deployment lifecycle persistence and
state-transition enforcement only. No provider adapters, no provider API
calls, no publish orchestration, no retries, no background jobs.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import (
    CampaignDeploymentNotFoundError,
    InvalidCampaignDeploymentStateError,
)
from app.core.providers import Provider
from app.models.campaign_deployment import CampaignDeployment, CampaignDeploymentStatus
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)

# The only lifecycle edges any method below may apply. Anything not listed
# here -- including any transition out of FAILED, which is terminal --
# raises InvalidCampaignDeploymentStateError.
#
# PENDING -> FAILED (added in Milestone 5): a publish attempt dispatched
# directly from PublishCampaignService can fail (adapter declines, or
# raises unexpectedly) before ever reaching SUBMITTED -- a deployment
# does not have to be provider-acknowledged first to fail.
_ALLOWED_TRANSITIONS: dict[
    CampaignDeploymentStatus, frozenset[CampaignDeploymentStatus]
] = {
    CampaignDeploymentStatus.PENDING: frozenset(
        {CampaignDeploymentStatus.SUBMITTED, CampaignDeploymentStatus.FAILED}
    ),
    CampaignDeploymentStatus.SUBMITTED: frozenset(
        {CampaignDeploymentStatus.LIVE, CampaignDeploymentStatus.FAILED}
    ),
    CampaignDeploymentStatus.LIVE: frozenset({CampaignDeploymentStatus.PAUSED}),
    CampaignDeploymentStatus.PAUSED: frozenset({CampaignDeploymentStatus.LIVE}),
    CampaignDeploymentStatus.FAILED: frozenset(),
}


class CampaignDeploymentService:
    """Orchestrates CampaignDeployment lifecycle transitions."""

    def __init__(
        self,
        deployment_repository: CampaignDeploymentRepository,
        session: Session,
    ) -> None:
        self._deployments = deployment_repository
        self._session = session

    def create_pending_deployment(
        self,
        *,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        provider: Provider,
        commit: bool = True,
    ) -> CampaignDeployment:
        """Create a new pending deployment for one campaign+provider pair.

        This is an initial-state creation -- there is no prior status to
        validate a transition from. Generates a fresh idempotency key.
        Does not check for an existing deployment on the same
        (campaign_id, provider) pair; that is enforced structurally by
        uq_campaign_deployments_campaign_id_provider, and a violation
        surfaces as an IntegrityError.

        When commit=False, stages/flushes only for an outer unit of work.
        """
        deployment = CampaignDeployment(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            provider=provider,
            idempotency_key=str(uuid.uuid4()),
            status=CampaignDeploymentStatus.PENDING,
        )
        try:
            self._deployments.create(deployment)
            self._finish_write(commit=commit, refresh=deployment)
        except Exception:
            self._abort_write(commit=commit)
            raise
        return deployment

    def mark_submitted(
        self,
        *,
        tenant_id: uuid.UUID,
        deployment_id: uuid.UUID,
        external_campaign_id: str,
        commit: bool = True,
    ) -> CampaignDeployment:
        """Transition pending -> submitted, recording the provider's ID.

        The transition UPDATE and the external_campaign_id UPDATE are
        one atomic unit under the caller's commit mode. When commit=True,
        a failure rolls back the whole method. When commit=False, the
        outer unit of work owns rollback.
        """
        try:
            deployment = self._apply_transition(
                tenant_id,
                deployment_id,
                CampaignDeploymentStatus.SUBMITTED,
                submitted_at=datetime.now(timezone.utc),
            )
            # Safe unconditionally: update_status's conditional UPDATE above
            # already succeeded inside this same open transaction, which
            # means we hold the row lock until commit -- no other transaction
            # can have changed this row since.
            self._deployments.update_external_reference(
                tenant_id, deployment_id, external_campaign_id
            )
            self._finish_write(commit=commit, refresh=deployment)
        except Exception:
            self._abort_write(commit=commit)
            raise
        return deployment

    def mark_live(
        self,
        *,
        tenant_id: uuid.UUID,
        deployment_id: uuid.UUID,
        commit: bool = True,
    ) -> CampaignDeployment:
        """Transition submitted -> live or paused -> live, recording
        confirmation time."""
        try:
            deployment = self._apply_transition(
                tenant_id,
                deployment_id,
                CampaignDeploymentStatus.LIVE,
                confirmed_at=datetime.now(timezone.utc),
            )
            self._finish_write(commit=commit, refresh=deployment)
        except Exception:
            self._abort_write(commit=commit)
            raise
        return deployment

    def mark_failed(
        self,
        *,
        tenant_id: uuid.UUID,
        deployment_id: uuid.UUID,
        last_error_message: str,
        commit: bool = True,
    ) -> CampaignDeployment:
        """Transition pending -> failed or submitted -> failed, recording
        the error message."""
        try:
            deployment = self._apply_transition(
                tenant_id,
                deployment_id,
                CampaignDeploymentStatus.FAILED,
                last_error_message=last_error_message,
            )
            self._finish_write(commit=commit, refresh=deployment)
        except Exception:
            self._abort_write(commit=commit)
            raise
        return deployment

    def mark_paused(
        self,
        *,
        tenant_id: uuid.UUID,
        deployment_id: uuid.UUID,
        commit: bool = True,
    ) -> CampaignDeployment:
        """Transition live -> paused."""
        try:
            deployment = self._apply_transition(
                tenant_id, deployment_id, CampaignDeploymentStatus.PAUSED
            )
            self._finish_write(commit=commit, refresh=deployment)
        except Exception:
            self._abort_write(commit=commit)
            raise
        return deployment

    def _finish_write(
        self, *, commit: bool, refresh: CampaignDeployment
    ) -> None:
        """Commit or flush after a successful write sequence."""
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        self._session.refresh(refresh)

    def _abort_write(self, *, commit: bool) -> None:
        """Rollback only when this service owns the transaction."""
        if commit:
            self._session.rollback()

    def _apply_transition(
        self,
        tenant_id: uuid.UUID,
        deployment_id: uuid.UUID,
        target_status: CampaignDeploymentStatus,
        **status_fields: object,
    ) -> CampaignDeployment:
        """Look up a deployment, validate target_status is a legal
        transition from its current status per _ALLOWED_TRANSITIONS, then
        apply it via an optimistic conditional UPDATE.

        This is a read-validate-write sequence, but the *write* re-checks
        status == (the exact status just read) at the database level via
        CampaignDeploymentRepository.update_status's WHERE clause. If a
        concurrent transition commits in between, this UPDATE's rowcount
        is 0 (its expected_current_status predicate no longer matches),
        and that -- not the earlier, now-stale Python-level check -- is
        what actually prevents a double transition. No SELECT ... FOR
        UPDATE / row locking is used: optimistic concurrency is
        sufficient because losing the race is cheap (raise and let the
        caller re-read), and there are no retries/background jobs in
        this phase's scope.
        """
        deployment = self._deployments.get_by_id(tenant_id, deployment_id)
        if deployment is None:
            raise CampaignDeploymentNotFoundError()

        current_status = deployment.status
        allowed = _ALLOWED_TRANSITIONS.get(current_status, frozenset())
        if target_status not in allowed:
            raise InvalidCampaignDeploymentStateError(
                f"Cannot transition deployment from {current_status.value} "
                f"to {target_status.value}."
            )

        affected = self._deployments.update_status(
            tenant_id,
            deployment_id,
            current_status,
            target_status,
            **status_fields,
        )
        if affected == 0:
            raise InvalidCampaignDeploymentStateError(
                f"Deployment was no longer in status {current_status.value} "
                "when the transition was applied; it changed concurrently."
            )
        return deployment

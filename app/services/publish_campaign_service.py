"""Publish orchestration for CampaignDeployment preparation + adapter dispatch.

Wires the adapter layer (ProviderAdapterRegistry / BaseAdapter /
PublishResult) and ProviderConnectionService into publish_campaign().
For every deployment that is still PENDING after the publish graph is
prepared, this service builds a CampaignSpec, resolves that deployment's
provider adapter, decrypts that tenant's connected provider credentials
via ProviderConnectionService.get_decrypted_credentials(), wraps them in
ProviderCredentials, and calls adapter.publish(spec, credentials) -- then
records the outcome via CampaignDeploymentService (mark_submitted on
success, mark_failed on failure or on any unexpected exception from the
credential lookup / adapter / spec-build step).

There is still no real Meta/Google API call, no OAuth, no HTTP, no
retries, no background jobs -- MetaAdapter/GoogleAdapter still raise
NotImplementedError (see app/adapters/meta_adapter.py and
google_adapter.py), so in practice every PENDING deployment currently
ends up FAILED with that message (or with a missing-connection /
decryption-failure message) until a future milestone gives the adapters
a real implementation.

Missing ProviderConnection and credential decryption failures are treated
the same as adapter exceptions: mark only that deployment FAILED and
continue processing remaining providers.

Deployments that were already past PENDING (submitted/live/paused/
failed) when the graph was prepared are returned exactly as found --
they are never re-published or reset by a later publish_campaign()
call.

list_deployments() is a pure read alongside publish_campaign() -- it
confirms the campaign belongs to the caller's tenant (the same rule
publish_campaign() itself applies) and returns that campaign's existing
deployments without creating, dispatching, or mutating anything. It
lives here rather than on CampaignDeploymentService because it needs
both CampaignRepository (ownership check) and CampaignDeploymentRepository
(the actual list), which this service already composes -- adding it to
CampaignDeploymentService would mean threading a CampaignRepository into
a constructor that has no other reason to depend on it.

PublishCampaignService is the only place allowed to construct
ProviderCredentials. Adapters never access repositories, sessions, env
vars, or decryption.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from app.adapters.registry import ProviderAdapterRegistry
from app.core.exceptions import CampaignNotFoundError, InvalidCampaignStateError
from app.core.logging_context import bound_context
from app.core.provider_credentials import ProviderCredentials
from app.core.provider_error_sanitization import (
    sanitize_provider_exception,
    sanitize_provider_message,
)
from app.core.providers import Provider
from app.models.campaign import Campaign, CampaignStatus
from app.models.campaign_deployment import CampaignDeployment, CampaignDeploymentStatus
from app.repositories.campaign_deployment_repository import (
    CampaignDeploymentRepository,
)
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_deployment_service import CampaignDeploymentService
from app.services.campaign_spec_builder import CampaignSpecBuilder
from app.services.provider_connection_service import ProviderConnectionService

# The full set of providers a campaign is deployed to. There is no
# per-campaign provider selection yet -- every campaign is prepared for
# every supported provider.
_SUPPORTED_PROVIDERS: tuple[Provider, ...] = (
    Provider.META,
    Provider.GOOGLE,
)

logger = logging.getLogger("verisure.publish_campaign")


class PublishCampaignService:
    """Orchestrates preparing and dispatching a campaign's publish.

    Composes CampaignRepository (read the campaign),
    CampaignDeploymentRepository (read existing deployments),
    CampaignDeploymentService (owns the commit for every deployment
    lifecycle transition unless commit=False for an outer unit of work),
    CampaignSpecBuilder, ProviderAdapterRegistry
    (resolve a provider's adapter), and ProviderConnectionService
    (decrypt credentials for adapter.publish).
    """

    def __init__(
        self,
        campaign_repository: CampaignRepository,
        deployment_repository: CampaignDeploymentRepository,
        deployment_service: CampaignDeploymentService,
        spec_builder: CampaignSpecBuilder,
        adapter_registry: ProviderAdapterRegistry,
        connection_service: ProviderConnectionService,
        session: Session,
    ) -> None:
        self._campaigns = campaign_repository
        self._deployments = deployment_repository
        self._deployment_service = deployment_service
        self._spec_builder = spec_builder
        self._adapter_registry = adapter_registry
        self._connections = connection_service
        self._session = session

    def publish_campaign(
        self,
        *,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        commit: bool = True,
    ) -> list[CampaignDeployment]:
        """Ensure a deployment exists for every supported provider, then
        attempt to publish each one that is still PENDING.

        Raises CampaignNotFoundError if the campaign does not exist for
        this tenant, or InvalidCampaignStateError if it is archived.
        Returns one CampaignDeployment per supported provider. A
        provider whose publish attempt raises for any reason (including
        an incomplete campaign spec, a missing provider connection, a
        credential decryption failure, an unknown provider, or the
        adapter itself) never prevents the remaining providers from
        being attempted -- see _attempt_provider_publish.

        When commit=False, deployment writes flush only and the caller
        owns the surrounding transaction (PublishJobService.run_once).
        """
        campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError()

        self._validate_publishable(campaign)

        with bound_context(
            campaign_id=str(campaign.id),
            tenant_id=str(tenant_id),
        ):
            logger.info("campaign_publish_started")

        deployments: list[CampaignDeployment] = []
        for provider in _SUPPORTED_PROVIDERS:
            deployment = self._deployments.get_by_campaign_and_provider(
                tenant_id, campaign.id, provider
            )
            if deployment is None:
                deployment = self._deployment_service.create_pending_deployment(
                    tenant_id=tenant_id,
                    campaign_id=campaign.id,
                    provider=provider,
                    commit=commit,
                )

            if deployment.status == CampaignDeploymentStatus.PENDING:
                deployment = self._attempt_provider_publish(
                    tenant_id, campaign, deployment, commit=commit
                )
                outcome = (
                    "submitted"
                    if deployment.status == CampaignDeploymentStatus.SUBMITTED
                    else "failed"
                )
                with bound_context(
                    provider=deployment.provider.value,
                    deployment_id=str(deployment.id),
                    campaign_id=str(campaign.id),
                    tenant_id=str(tenant_id),
                    status=outcome,
                ):
                    logger.info("provider_publish_finished")

            deployments.append(deployment)

        return deployments

    def list_deployments(
        self, *, tenant_id: uuid.UUID, campaign_id: uuid.UUID
    ) -> list[CampaignDeployment]:
        """Return every deployment for one campaign, in deterministic order.

        Raises CampaignNotFoundError if the campaign does not exist for
        this tenant -- including a cross-tenant lookup, deliberately
        indistinguishable from a genuinely missing campaign, matching
        publish_campaign's own rule. Does not create, dispatch, or
        mutate anything.
        """
        campaign = self._campaigns.get_by_tenant_and_id(tenant_id, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError()
        return self._deployments.list_by_campaign(tenant_id, campaign.id)

    def _attempt_provider_publish(
        self,
        tenant_id: uuid.UUID,
        campaign: Campaign,
        deployment: CampaignDeployment,
        *,
        commit: bool,
    ) -> CampaignDeployment:
        """Build a spec, resolve credentials + adapter, call
        adapter.publish(spec, credentials), and record the outcome.

        Exception boundary: only failures from the provider-facing
        sequence itself (CampaignSpecBuilder validation,
        ProviderConnectionService.get_decrypted_credentials,
        ProviderAdapterRegistry.get raising ValueError for an unknown
        provider, or the adapter raising, expectedly or not -- including
        a PublishResult constructed with an invalid
        success/external_campaign_id/error_message combination, which
        raises ValueError from inside adapter.publish) are caught here
        and turned into a mark_failed transition, so that one provider's
        failure never prevents the remaining providers from being
        attempted by the caller's loop.

        This try/except deliberately does NOT wrap the mark_submitted /
        mark_failed calls that record the outcome: those are persistence
        operations, not provider-boundary failures, and
        CampaignDeploymentService already rolls back (when commit=True)
        and re-raises on any failure of its own (including a failure
        *while recording* a failure). Letting that propagate uncaught
        here -- rather than catching it and mislabeling it as "this
        provider failed" -- is intentional: a persistence/database
        failure is a different kind of problem than a provider declining
        a campaign, and swallowing it would both hide a real outage and
        leave the caller's loop proceeding to the next provider on top
        of a transaction that may not be trustworthy. So publish_campaign
        does NOT attempt the remaining providers in that case -- it
        stops and propagates.

        Decrypted credential bytes never leave this method except inside
        a ProviderCredentials value passed to adapter.publish.
        """
        try:
            spec = self._spec_builder.build(campaign, deployment)
            adapter = self._adapter_registry.get(deployment.provider)
            credential_payload = self._connections.get_decrypted_credentials(
                tenant_id=tenant_id,
                provider=deployment.provider,
            )
            credentials = ProviderCredentials(
                provider=deployment.provider,
                credential_payload=credential_payload,
            )
            result = adapter.publish(spec, credentials)
        except Exception as exc:
            return self._deployment_service.mark_failed(
                tenant_id=tenant_id,
                deployment_id=deployment.id,
                last_error_message=sanitize_provider_exception(exc),
                commit=commit,
            )

        if result.success:
            return self._deployment_service.mark_submitted(
                tenant_id=tenant_id,
                deployment_id=deployment.id,
                external_campaign_id=result.external_campaign_id,
                commit=commit,
            )

        return self._deployment_service.mark_failed(
            tenant_id=tenant_id,
            deployment_id=deployment.id,
            last_error_message=sanitize_provider_message(result.error_message),
            commit=commit,
        )

    @staticmethod
    def _validate_publishable(campaign: Campaign) -> None:
        """Reject campaigns that cannot be published in their current state.

        Only archived campaigns are rejected today. Milestone 1 has no
        "ready" transition yet (see app/models/campaign.py), so a draft
        campaign is publishable as far as this method is concerned --
        campaign *completeness* (objective/budget/schedule) is checked
        by CampaignSpecBuilder inside _attempt_provider_publish, and an
        incomplete campaign surfaces as a per-provider mark_failed
        rather than blocking publish_campaign entirely.
        """
        if campaign.status == CampaignStatus.ARCHIVED:
            raise InvalidCampaignStateError(
                "An archived campaign cannot be published."
            )

"""Standalone async-publish worker (Async Publish Phase 4).

Entry point:

    python -m app.orchestration.publish_worker

Constructs the normal repository/service graph directly against one
SQLAlchemy Session per polling iteration. Never imports FastAPI, never
touches routers/Request objects, and never calls HTTP APIs -- the worker
boundary is PublishJobService.run_once() only.

Observability: worker_started / worker_idle (DEBUG) /
unexpected_worker_error only. Business publish failures are logged once
inside PublishJobService and are not re-logged here.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.adapters.registry import ProviderAdapterRegistry
from app.core.logging import configure_logging
from app.core.logging_context import clear
from app.core.security.credential_encryption import CredentialEncryptionService
from app.core.settings import settings
from app.core.startup import validate_startup_config
from app.database.session import SessionFactory
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
from app.services.publish_job_service import (
    PublishJobService,
    consume_business_failure_logged,
)

logger = logging.getLogger("verisure.worker")


def build_publish_job_service(session: Session) -> PublishJobService:
    """Wire one Session into the production publish-job service graph."""
    job_repository = PublishJobRepository(session)
    campaign_repository = CampaignRepository(session)
    deployment_repository = CampaignDeploymentRepository(session)
    connection_repository = ProviderConnectionRepository(session)

    encryption_service = CredentialEncryptionService(settings.encryption_key)
    deployment_service = CampaignDeploymentService(deployment_repository, session)
    connection_service = ProviderConnectionService(
        connection_repository, encryption_service, session
    )
    publish_campaign_service = PublishCampaignService(
        campaign_repository,
        deployment_repository,
        deployment_service,
        CampaignSpecBuilder(),
        ProviderAdapterRegistry(),
        connection_service,
        session,
    )
    return PublishJobService(
        job_repository,
        campaign_repository,
        publish_campaign_service,
        session,
    )


def run_once() -> bool:
    """Perform exactly one polling iteration with a fresh Session.

    Creates the service graph, invokes PublishJobService.run_once(), and
    always closes the session. Returns whatever PublishJobService.run_once
    returned (True if a job was claimed and driven to a terminal attempt,
    False if the queue was empty). Does not catch exceptions from the
    service -- callers that need loop resilience (run_forever) handle that.
    """
    session = SessionFactory()()
    try:
        service = build_publish_job_service(session)
        return service.run_once()
    finally:
        clear()
        session.close()


def run_forever() -> None:
    """Poll forever: process one job per iteration, sleep when idle/errored.

    A single job failure must not terminate the worker. Session lifetime is
    owned by run_once() (one Session per iteration, always closed).
    """
    logger.info("worker_started")
    interval = settings.publish_job_poll_interval_seconds
    while True:
        try:
            processed = run_once()
            if not processed:
                logger.debug("worker_idle")
                time.sleep(interval)
        except Exception:
            if not consume_business_failure_logged():
                logger.error("unexpected_worker_error", exc_info=True)
            time.sleep(interval)


def main() -> None:
    """Module entry point for ``python -m app.orchestration.publish_worker``."""
    validate_startup_config()
    configure_logging()
    run_forever()


if __name__ == "__main__":
    main()

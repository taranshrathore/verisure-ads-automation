"""Authorization dependency factories for route declarations.

These factories perform zero database queries and contain zero bypass
logic: they receive the request-cached AuthorizationContext and delegate
the check to AuthorizationService, which raises PermissionDeniedError on
failure (translated to 403 by the registered exception handler).

Usage at an endpoint:

    @router.get("/campaigns", dependencies=[Depends(require_permission(
        PermissionSlug.CAMPAIGNS_READ))])

or, when the endpoint also needs the context:

    context: AuthorizationContext = Depends(require_permission(
        PermissionSlug.CAMPAIGNS_READ))
"""

from collections.abc import Callable

from fastapi import Depends

from app.api.dependencies import get_authorization_context, get_authorization_service
from app.core.authorization.catalog import PermissionSlug, SystemRoleSlug
from app.core.authorization.context import AuthorizationContext
from app.services.authorization_service import AuthorizationService


def require_permission(
    permission: PermissionSlug,
) -> Callable[..., AuthorizationContext]:
    """Return a dependency enforcing an effective tenant-permission check."""

    def dependency(
        context: AuthorizationContext = Depends(get_authorization_context),
        authorization_service: AuthorizationService = Depends(
            get_authorization_service
        ),
    ) -> AuthorizationContext:
        authorization_service.require(context, permission)
        return context

    return dependency


def require_system_role(
    system_role: SystemRoleSlug,
) -> Callable[..., AuthorizationContext]:
    """Return a dependency enforcing an exact system-role check.

    The check is context membership, which already encodes the
    platform-tenant eligibility rule; route naming carries no security
    weight.
    """

    def dependency(
        context: AuthorizationContext = Depends(get_authorization_context),
        authorization_service: AuthorizationService = Depends(
            get_authorization_service
        ),
    ) -> AuthorizationContext:
        authorization_service.require_system_role(context, system_role)
        return context

    return dependency

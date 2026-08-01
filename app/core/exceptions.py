"""Framework-agnostic application exception hierarchy."""


class AppError(Exception):
    """Base class for all application-specific exceptions."""

    def __init__(self, message: str = "Application error.") -> None:
        self.message = message
        super().__init__(message)


class AuthenticationError(AppError):
    """Base class for authentication-related failures."""


class InvalidCredentialsError(AuthenticationError):
    """Raised when tenant, email, or password verification fails."""

    def __init__(self, message: str = "Invalid tenant, email, or password.") -> None:
        super().__init__(message)


class InvalidAccessTokenError(AuthenticationError):
    """Raised when a JWT access token is missing, malformed, or invalid."""

    def __init__(self, message: str = "Invalid or expired access token.") -> None:
        super().__init__(message)


class InvalidRefreshTokenError(AuthenticationError):
    """Raised when a refresh token cannot be found or is malformed."""

    def __init__(self, message: str = "Invalid refresh token.") -> None:
        super().__init__(message)


class RefreshTokenExpiredError(AuthenticationError):
    """Raised when a refresh token has passed its expiry time."""

    def __init__(self, message: str = "Refresh token has expired.") -> None:
        super().__init__(message)


class RefreshTokenRevokedError(AuthenticationError):
    """Raised when a refresh token has already been revoked."""

    def __init__(self, message: str = "Refresh token has been revoked.") -> None:
        super().__init__(message)


class RefreshTokenReuseError(AuthenticationError):
    """Raised when an already-rotated refresh token is reused."""

    def __init__(
        self, message: str = "Refresh token reuse detected; session revoked."
    ) -> None:
        super().__init__(message)


class TenantNotFoundError(AppError):
    """Raised when a tenant cannot be found."""

    def __init__(self, message: str = "Tenant not found.") -> None:
        super().__init__(message)


class TenantInactiveError(AppError):
    """Raised when a tenant exists but is soft-deleted or disabled."""

    def __init__(self, message: str = "Tenant is inactive.") -> None:
        super().__init__(message)


class UserNotFoundError(AppError):
    """Raised when a user cannot be found."""

    def __init__(self, message: str = "User not found.") -> None:
        super().__init__(message)


class UserInactiveError(AppError):
    """Raised when a user exists but is soft-deleted or disabled."""

    def __init__(self, message: str = "User is inactive.") -> None:
        super().__init__(message)


class CampaignNotFoundError(AppError):
    """Raised when a campaign cannot be found within the caller's tenant.

    Also used for cross-tenant lookups (a campaign that exists but belongs
    to a different tenant) -- deliberately indistinguishable from a
    genuinely missing campaign, to avoid leaking cross-tenant existence.
    """

    def __init__(self, message: str = "Campaign not found.") -> None:
        super().__init__(message)


class InvalidCampaignStateError(AppError):
    """Raised when an operation is attempted against a campaign whose
    current lifecycle state does not allow it (e.g. editing or archiving
    a campaign that is no longer a draft).
    """

    def __init__(
        self, message: str = "Campaign is not in a valid state for this operation."
    ) -> None:
        super().__init__(message)


class CampaignValidationError(AppError):
    """Raised when campaign field values fail domain validation (e.g. an
    incomplete budget triple, a non-positive amount, a malformed currency
    code, or an invalid schedule) before any database write is attempted.
    """

    def __init__(self, message: str = "Invalid campaign data.") -> None:
        super().__init__(message)


class CampaignDeploymentNotFoundError(AppError):
    """Raised when a deployment cannot be found within the caller's tenant.

    Also used for cross-tenant lookups (a deployment that exists but
    belongs to a different tenant) -- deliberately indistinguishable from
    a genuinely missing deployment, matching CampaignNotFoundError's
    existing pattern, to avoid leaking cross-tenant existence.
    """

    def __init__(self, message: str = "Campaign deployment not found.") -> None:
        super().__init__(message)


class InvalidCampaignDeploymentStateError(AppError):
    """Raised when a lifecycle transition is attempted against a
    deployment whose current status does not allow it (e.g. marking a
    pending deployment live without first marking it submitted).
    """

    def __init__(
        self,
        message: str = "Campaign deployment is not in a valid state for this operation.",
    ) -> None:
        super().__init__(message)


# NOTE: The local RBAC exception hierarchy (AuthorizationError,
# PermissionDeniedError, CrossTenantAccessError, RoleNotFoundError,
# PermissionNotFoundError, RoleAssignmentConflictError, ProtectedRoleError,
# LastTenantAdminError, PlatformTenantRequiredError) was removed along with
# the local RBAC engine -- see docs/HANDOFF.md for the CRM migration
# status. Reintroduce authorization-failure exceptions once the CRM
# authorization contract is known; do not guess at their shape now.

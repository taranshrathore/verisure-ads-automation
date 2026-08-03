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


class ProviderConnectionNotFoundError(AppError):
    """Raised when a provider connection cannot be found within the
    caller's tenant.

    Also used for cross-tenant lookups (a connection that exists but
    belongs to a different tenant) -- deliberately indistinguishable
    from a genuinely missing connection, matching CampaignNotFoundError's
    existing pattern, to avoid leaking cross-tenant existence. Also used
    by get_decrypted_credentials for a disconnected/missing/cross-tenant
    provider, for the same reason.
    """

    def __init__(self, message: str = "Provider connection not found.") -> None:
        super().__init__(message)


class ProviderConnectionAlreadyExistsError(AppError):
    """Raised by connect() when the caller's tenant already has a
    CONNECTED row for the requested provider.

    uq_provider_connections_tenant_id_provider guarantees at most one row
    per (tenant_id, provider) ever exists; this is specifically the
    already-connected case of that pair (see
    InvalidProviderConnectionStateError for the already-disconnected
    case).
    """

    def __init__(
        self, message: str = "A connection to this provider already exists."
    ) -> None:
        super().__init__(message)


class InvalidProviderConnectionStateError(AppError):
    """Raised when an operation is attempted against a provider connection
    whose current state does not allow it: connect() targeting a
    (tenant, provider) pair that already has a DISCONNECTED row (this
    milestone intentionally keeps disconnected terminal -- reconnection
    is not implemented yet), or disconnect() targeting a row that is not
    currently CONNECTED (already disconnected, or changed concurrently
    since the caller's read).
    """

    def __init__(
        self,
        message: str = "Provider connection is not in a valid state for this operation.",
    ) -> None:
        super().__init__(message)


class CredentialEncryptionUnavailableError(AppError):
    """Raised when CredentialEncryptionService cannot be constructed or used
    because ENCRYPTION_KEY is missing, blank, or malformed.

    This is a configuration/deployment problem, not a per-request input
    error -- credential encryption fails closed rather than ever falling
    back to storing or returning plaintext.
    """

    def __init__(self, message: str = "Credential encryption is unavailable.") -> None:
        super().__init__(message)


class CredentialDecryptionError(AppError):
    """Raised when stored ciphertext cannot be turned back into credential
    bytes: wrong/rotated-out key, tampered ciphertext, malformed envelope
    JSON/base64, an unsupported envelope version, or an envelope whose
    tenant_id/provider does not match the requested context.

    The message is always a short, generic string -- it deliberately never
    distinguishes *why* decryption failed, since doing so would help an
    attacker probing stored ciphertext, and it never contains plaintext,
    ciphertext, or key material.
    """

    def __init__(self, message: str = "Unable to decrypt stored credentials.") -> None:
        super().__init__(message)


class PublishJobNotFoundError(AppError):
    """Raised when a publish job cannot be found within the caller's
    tenant and campaign.

    Also used for cross-tenant lookups and for a job that exists under
    the same tenant but a different campaign -- deliberately
    indistinguishable from a genuinely missing job, matching
    CampaignNotFoundError's existing pattern.
    """

    def __init__(self, message: str = "Publish job not found.") -> None:
        super().__init__(message)


# NOTE: The local RBAC exception hierarchy (AuthorizationError,
# PermissionDeniedError, CrossTenantAccessError, RoleNotFoundError,
# PermissionNotFoundError, RoleAssignmentConflictError, ProtectedRoleError,
# LastTenantAdminError, PlatformTenantRequiredError) was removed along with
# the local RBAC engine -- see docs/HANDOFF.md for the CRM migration
# status. Reintroduce authorization-failure exceptions once the CRM
# authorization contract is known; do not guess at their shape now.

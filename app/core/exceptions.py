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


class AuthorizationError(AppError):
    """Base class for authorization-related failures."""

    def __init__(self, message: str = "Authorization failed.") -> None:
        super().__init__(message)


class PermissionDeniedError(AuthorizationError):
    """Raised when a caller lacks a required permission."""

    def __init__(self, message: str = "Permission denied.") -> None:
        super().__init__(message)


class CrossTenantAccessError(AuthorizationError):
    """Raised when a caller attempts to access another tenant's resource."""

    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(message)


class RoleNotFoundError(AppError):
    """Raised when a role cannot be found."""

    def __init__(self, message: str = "Role not found.") -> None:
        super().__init__(message)


class PermissionNotFoundError(AppError):
    """Raised when a permission cannot be found."""

    def __init__(self, message: str = "Permission not found.") -> None:
        super().__init__(message)


class RoleAssignmentConflictError(AppError):
    """Raised when a role assignment conflicts with an existing one."""

    def __init__(self, message: str = "Role assignment already exists.") -> None:
        super().__init__(message)


class ProtectedRoleError(AppError):
    """Raised when a protected built-in role is modified."""

    def __init__(self, message: str = "Protected role cannot be modified.") -> None:
        super().__init__(message)


class LastTenantAdminError(AppError):
    """Raised when an operation would remove a tenant's final administrator."""

    def __init__(
        self, message: str = "A tenant must retain at least one active administrator."
    ) -> None:
        super().__init__(message)


class PlatformTenantRequiredError(AppError):
    """Raised when a system-role grant targets a user outside the platform tenant."""

    def __init__(
        self,
        message: str = "System roles may only be granted to platform-tenant users.",
    ) -> None:
        super().__init__(message)

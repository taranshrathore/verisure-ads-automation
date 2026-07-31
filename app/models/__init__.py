"""ORM model package.

Importing this package registers all models on Base.metadata for Alembic.
"""

from app.models.tenant import Tenant
from app.models.user import User

__all__ = ["Tenant", "User"]

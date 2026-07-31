"""Temporary smoke test for Tenant and User ORM models against PostgreSQL."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.session import SessionFactory
from app.models.tenant import Tenant
from app.models.user import User


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    session = SessionFactory()()

    try:
        tenant = Tenant(
            name=f"Smoke Test Tenant {suffix}",
            slug=f"smoke-test-{suffix}",
        )
        session.add(tenant)
        session.flush()

        user = User(
            tenant_id=tenant.id,
            email=f"user-{suffix}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
        )
        session.add(user)
        session.commit()
        print("Created tenant and user successfully.")

        loaded_tenant = session.scalar(
            select(Tenant).where(Tenant.slug == f"smoke-test-{suffix}")
        )
        if loaded_tenant is None:
            raise AssertionError("Tenant was not found after commit.")
        if len(loaded_tenant.users) != 1:
            raise AssertionError(
                f"Expected exactly one user, found {len(loaded_tenant.users)}."
            )
        loaded_user = loaded_tenant.users[0]
        if loaded_user.tenant_id != loaded_tenant.id:
            raise AssertionError("User.tenant_id does not match Tenant.id.")
        if loaded_user.role != "member":
            raise AssertionError(f"Expected role 'member', got {loaded_user.role!r}.")
        print("Tenant/user relationship checks passed.")

        duplicate = User(
            tenant_id=loaded_tenant.id,
            email=f"user-{suffix}@example.com",
            hashed_password="not-a-real-password-hash",
            role="member",
        )
        session.add(duplicate)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            print("Tenant-scoped email unique constraint behaved as expected.")
        else:
            raise AssertionError(
                "Expected IntegrityError for duplicate (tenant_id, email), "
                "but commit succeeded."
            )

        session.delete(loaded_user)
        session.delete(loaded_tenant)
        session.commit()
        print("Cleanup complete: smoke-test tenant and user deleted.")
        print("All model smoke tests passed.")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()

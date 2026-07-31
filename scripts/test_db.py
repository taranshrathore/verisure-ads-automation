"""Temporary PostgreSQL connectivity test."""

from sqlalchemy import text

from app.database.session import SessionFactory


def main() -> None:
    session = None
    try:
        session = SessionFactory()()
        result = session.execute(text("SELECT version();"))
        version = result.scalar_one()
        print(version)
    except Exception as exc:
        print(f"Database connectivity test failed: {exc!r}")
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    main()

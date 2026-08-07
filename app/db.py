from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def with_psycopg_driver(database_url: str) -> str:
    # Managed Postgres providers (Render's `connectionString` included) hand
    # out a bare `postgres://`/`postgresql://` URL with no driver suffix,
    # which makes SQLAlchemy default to psycopg2 — not installed here, only
    # psycopg3 is (see pyproject.toml). Normalize to `+psycopg` unless a
    # driver is already specified (local dev's default, SQLite test URLs).
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


engine = create_engine(with_psycopg_driver(settings.database_url), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

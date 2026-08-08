from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DMS_", env_file=".env", extra="ignore")

    # Postgres is the target production database. Tests run against SQLite
    # in-memory (see tests/conftest.py) since this shell only uses portable
    # column types (string UUIDs, JSON) — no Postgres-only features yet.
    database_url: str = "postgresql+psycopg://dms:dms@localhost:5432/dms_platform"

    # Placeholder JWT signing secret until a real external IdP is selected
    # (see DMS_MDM_V1_SPEC.md cross-cutting #10: auth_identity_id is a
    # placeholder FK). This lets the auth boundary + access_role gating be
    # built and tested now without blocking on an IdP decision.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "dms-platform"
    jwt_access_token_ttl_seconds: int = 3600

    pagination_default_limit: int = 50
    pagination_max_limit: int = 100

    # U-07: exact count under this many matching rows, "at least N" above
    # it — a full COUNT(*) on a filtered 100k-row table must never delay
    # the first page (UI/UX Core Principles § FR-UI-04).
    count_exact_threshold: int = 10_000

    # Frontend dev server origin(s) allowed to make credentialed requests
    # (cookie-based session, issue #8) — must be an explicit origin list,
    # not "*", since the session cookie relies on Access-Control-Allow-
    # Credentials being paired with a specific Access-Control-Allow-Origin.
    cors_allowed_origins: list[str] = ["http://localhost:5173"]

    idempotency_key_ttl_seconds: int = 86400

    # Field-level encryption for Dealer.tax_id (Swiss addendum tax_id
    # requirement + spec open question 8). Single static Fernet key from
    # settings, not a KMS-backed per-tenant key — real key-management
    # (rotation, HSM/KMS-backed storage) is still an open decision per that
    # question. No default on purpose: an earlier draft hardcoded a live
    # key here (now burned, since it landed in git history), which made
    # "forgot to set the env var" silently equivalent to "not encrypted at
    # all". Missing env var must fail app startup, not fall back quietly.
    tax_id_encryption_key: str


@lru_cache
def get_settings() -> Settings:
    return Settings()

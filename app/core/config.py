from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.secrets import resolve_secret_env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DMS_", env_file=".env", extra="ignore")

    # Postgres is the target production database. Tests run against SQLite
    # in-memory (see tests/conftest.py) since this shell only uses portable
    # column types (string UUIDs, JSON) — no Postgres-only features yet.
    database_url: str = "postgresql+psycopg://dms:dms@localhost:5432/dms_platform"

    # RS256 private key (PEM, PKCS8) until a real external IdP is selected
    # (see DMS_MDM_V1_SPEC.md cross-cutting #10: auth_identity_id is a
    # placeholder FK). This lets the auth boundary + access_role gating be
    # built and tested now without blocking on an IdP decision.
    #
    # Asymmetric, not the old shared HS256 secret (ADR-007, WP-2 PR-1):
    # a shared secret is fine for one process, but the moment a second
    # service needs to verify a token, it either gets a copy of the secret
    # it could also use to mint tokens, or the whole scheme is unsafe. With
    # RS256 every service verifies with the public key (published as JWKS,
    # see app.core.auth.get_jwks) and only this process ever holds the
    # private key. No default on purpose, same reasoning as
    # tax_id_encryption_key below: a missing key must fail startup, not
    # silently mint tokens nobody else can ever legitimately verify.
    jwt_private_key: str
    jwt_issuer: str = "dms-platform"
    jwt_access_token_ttl_seconds: int = 3600

    # Zitadel OIDC (WP-4, ADR-016/ADR-007): the external IdP authenticates;
    # it never assigns roles or is_dealer_manager (app.platform.services.oidc,
    # app.platform.models.user). No defaults on any of the three, same
    # "missing must fail startup" posture as jwt_private_key above — a
    # missing client_secret must never fall back to an unauthenticated or
    # misconfigured OIDC client.
    zitadel_issuer: str
    zitadel_client_id: str
    zitadel_client_secret: str
    # The absolute callback URL registered with the Zitadel application —
    # deliberately explicit config, not derived from the incoming request
    # (request.url_for) at call time. This process runs behind Render's
    # proxy with no --proxy-headers/X-Forwarded-Proto handling configured
    # in uvicorn, so a request-derived URL would silently resolve to the
    # wrong scheme (http instead of https) — exactly the kind of mismatch
    # an OIDC provider rejects with a generic, hard-to-debug error.
    zitadel_redirect_uri: str
    # Starlette SessionMiddleware's signing key (app/main.py) — holds the
    # transient OIDC state/nonce/PKCE verifier across the redirect round
    # trip to Zitadel and back, nothing else. No default, same reasoning:
    # a leaked/predictable key here is a CSRF hole in the login flow itself.
    session_secret_key: str
    # Where the OIDC callback sends the browser after minting a session.
    # Empty = same-origin relative redirect ("/") — correct for
    # staging/prod, which serve the built SPA from this same FastAPI app
    # (app/main.py). Local dev sets an absolute URL, since the Vite dev
    # server runs on a different origin/port.
    post_login_redirect_base_url: str = ""

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

    # Field-level encryption for Dealership.tax_id (Swiss addendum tax_id
    # requirement + spec open question 8). Single static Fernet key from
    # settings, not a KMS-backed per-tenant key — real key-management
    # (rotation, HSM/KMS-backed storage) is still an open decision per that
    # question. No default on purpose: an earlier draft hardcoded a live
    # key here (now burned, since it landed in git history), which made
    # "forgot to set the env var" silently equivalent to "not encrypted at
    # all". Missing env var must fail app startup, not fall back quietly.
    tax_id_encryption_key: str

    # Observability (WP-2 PR-3, closes G-16). Every one of these is
    # optional and unset by default — dev/test must work with zero
    # observability infra configured, so absence means "off", not "fail
    # startup" (unlike the two secrets above, where absence is the bug).
    environment: str = "development"
    otel_service_name: str = "dms-platform"
    # Grafana Cloud's OTLP endpoint + `Authorization: Basic ...`-style
    # header, comma-separated "key=value" pairs (the OTel SDK's own
    # convention for OTEL_EXPORTER_OTLP_HEADERS). Tracing/metrics stay
    # fully no-op — not buffered, not dropped-with-a-warning, genuinely
    # inert — until this is set; the OTel API returns no-op
    # tracers/meters when no SDK provider has been registered.
    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: str | None = None
    sentry_dsn: str | None = None

    # WP-5 PR-7 (ADR-021, risk A-13). Now defaults **True** (flipped WP-5
    # PR-3): the one-way migration (scripts/migrate_legacy_vehicles.py)
    # has shipped, the three-layer model is the write path, and the legacy
    # `vehicle` write endpoints return 409 pointing at /v1/vehicle-mdm.
    # **Reads keep working and the table is never dropped** — this flag is
    # cutover, not retirement (retirement waits for the seven-consecutive-
    # clean-nights reconciliation gate). Set to False only to re-open the
    # legacy write API during an incident.
    legacy_vehicle_write_frozen: bool = True


@lru_cache
def get_settings() -> Settings:
    # WP-2 PR-4 (closes G-10): populates the env var from Infisical BEFORE
    # Settings() reads it, if it isn't already set directly — a no-op when
    # DMS_INFISICAL_* isn't configured (every test run, most of local dev)
    # or when the plain env var is already present. Settings() below is
    # completely unaware this happened; it just sees an env var, same as
    # always.
    resolve_secret_env("DMS_TAX_ID_ENCRYPTION_KEY", infisical_secret_name="TAX_ID_ENCRYPTION_KEY")
    resolve_secret_env("DMS_JWT_PRIVATE_KEY", infisical_secret_name="JWT_PRIVATE_KEY")
    resolve_secret_env("DMS_ZITADEL_CLIENT_SECRET", infisical_secret_name="ZITADEL_CLIENT_SECRET")
    resolve_secret_env("DMS_SESSION_SECRET_KEY", infisical_secret_name="SESSION_SECRET_KEY")

    # tax_id_encryption_key, jwt_private_key, zitadel_client_secret and
    # session_secret_key have no default (see their own comments above) but
    # aren't actually missing here — pydantic-settings populates all four
    # from the environment at runtime. mypy has no visibility into that.
    return Settings()  # type: ignore[call-arg]

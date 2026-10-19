from functools import lru_cache

from pydantic import Field
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

    # KAN-196 (D-A-07, D-A-12): Nexotec owns authentication. argon2id policy
    # — the parameters new hashes are written with and the floor a stored
    # hash is rehashed up to on login (Authentication & Identity,
    # Credentials). Tuned on the build machine for ~250 ms a verify
    # (scripts/benchmark_argon2.py prints the measurement); the Render
    # instance is re-tuned in a follow-up ticket (Anto, 2026-10-17).
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_kib: int = Field(default=65536, ge=8192)
    argon2_parallelism: int = Field(default=4, ge=1)
    # FR-AU-05: the Have I Been Pwned range check at set-time. Fail-open and
    # flagged when the service does not answer within the timeout.
    hibp_enabled: bool = True
    hibp_timeout_seconds: float = Field(default=2.0, gt=0)
    # FR-AU-08/09: soft lockout on the credential after this many failures,
    # for this long; both axes back off exponentially before that.
    auth_lockout_failures: int = Field(default=5, ge=1)
    auth_lockout_minutes: int = Field(default=15, ge=1)
    auth_ip_window_minutes: int = Field(default=15, ge=1)
    # FR-AU-17/19 (Anto, 2026-10-17): two hours idle; the absolute end is the
    # next midnight in this zone — never a fixed 24 h that cuts a user off
    # mid-configuration. Per-Betrieb policy inside a platform floor and
    # ceiling is D-A-37, [P3].
    session_idle_minutes: int = Field(default=120, ge=1)
    session_timezone: str = "Europe/Zurich"
    # FR-AU-15 / D-A-28: platform mail (invitations, resets) leaves through a
    # Nexotec-owned mailbox behind the sender seam (Anto, 2026-10-17).
    # "logging" writes the message to the application log — the default for
    # dev, test and any environment without a mailbox; "smtp" sends it.
    platform_mail_sender: str = "logging"
    platform_mail_from: str = "no-reply@nexotec.example"
    platform_mail_smtp_host: str = ""
    platform_mail_smtp_port: int = 587
    platform_mail_smtp_username: str = ""
    platform_mail_smtp_password: str = ""
    platform_mail_smtp_starttls: bool = True
    # Where the e-mails' links point: the SPA origin (same origin as the API
    # on staging/prod, the Vite dev server locally).
    app_base_url: str = "http://localhost:5173"
    # WebAuthn relying party (passkeys, A-13). The RP id is the site's
    # registrable domain; the origin must match the browser's exactly.
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Nexotec DMS"
    webauthn_origin: str = "http://localhost:5173"

    # KAN-200 (Anto, 2026-10-19): the object store behind
    # app.core.object_store — signature images today. "local" keeps files
    # under `object_store_local_dir` (dev, test, staging); an S3-compatible
    # bucket is a follow-up with its own dependency decision.
    object_store_backend: str = "local"
    object_store_local_dir: str = "var/objects"

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

    # KAN-231 (ADR-039, PRD Vehicles Halterauskunftssperre, Anto's "log and
    # limit" ruling 2026-10-10): one user may read the plate history of at
    # most this many DISTINCT vehicles per rolling window; a vehicle already
    # read inside the window does not count again. Platform-wide by design:
    # the plates are third parties' personal data on a shared, non-tenant
    # vehicle record, so no dealership may loosen it (not an A-RULE-0
    # dealership setting).
    # 0 refuses every new vehicle (a lock-down, or to demonstrate the
    # refusal); a negative limit or an empty window is a misconfiguration.
    plate_read_limit: int = Field(default=60, ge=0)
    plate_read_window_seconds: int = Field(default=3600, gt=0)

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
    resolve_secret_env("DMS_PLATFORM_MAIL_SMTP_PASSWORD", infisical_secret_name="PLATFORM_MAIL_SMTP_PASSWORD")

    # tax_id_encryption_key and jwt_private_key have no default (see their own
    # comments above) but aren't actually missing here — pydantic-settings
    # populates both from the environment at runtime. mypy has no visibility
    # into that.
    return Settings()  # type: ignore[call-arg]

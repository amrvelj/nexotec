"""KAN-226 PR-2 (D-A-32, FR-A-35): the Google Business Profile channel's
provider, link states and location bindings.

- `integration_provider`: `google_business_profile`, category `marketplace`,
  platform scope only — Nexotec's own OAuth client at Google (config
  `clientId`, slot `client_secret`; scope `business.manage`). No sandbox.
- `integration_gbp_link_state`: one pending OAuth link (a hash of the state).
- `integration_gbp_location_binding`: one Nexotec location bound to one Google
  location; one live binding per Nexotec location and per Google location
  (partial unique indexes). Its refresh token lives in the secrets manager.

The downgrade refuses while a connection to the provider or a binding that is
not disconnected exists: both hold references to secret material the
downgrade cannot reach (the KAN-550 rule) — delete or disconnect them first.

Revision ID: b2be6d0c6a85
Revises: 2301d25c7b07
Create Date: 2026-11-20 14:08:58.682836

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from app.core.types import GUID, UTCDateTime
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "b2be6d0c6a85"
down_revision: str | Sequence[str] | None = "2301d25c7b07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDER_CODE = "google_business_profile"
PROVIDER_ROW = {
    "provider_code": PROVIDER_CODE,
    "category": "marketplace",
    "display_name": "Google Business Profile",
    "auth_type": "oauth2",
    "required_secret_slots": ["client_secret"],
    "required_config_keys": ["clientId"],
    "capability_codes": ["business_profile"],
    "docs_url": "https://developers.google.com/my-business/reference/businessinformation/rest",
    "supports_sandbox": False,
}

_BINDING_LIVE = "status <> 'disconnected'"
_GOOGLE_LIVE = "status <> 'disconnected' AND google_location_name IS NOT NULL"


def upgrade() -> None:
    op.create_table(
        "integration_gbp_link_state",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "tenant_id", GUID(), nullable=False, comment="Owned by the platform context (Dealership.id). No DB-level FK."
        ),
        sa.Column("user_id", GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."),
        sa.Column(
            "location_id", GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
        ),
        sa.Column("expires_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        op.f("ix_integration_gbp_link_state_tenant_id"), "integration_gbp_link_state", ["tenant_id"], unique=False
    )
    op.create_table(
        "integration_gbp_location_binding",
        sa.Column(
            "location_id", GUID(), nullable=False, comment="Owned by the platform context (Location.id). No DB-level FK."
        ),
        sa.Column("location_label", sa.String(length=200), nullable=False),
        sa.Column("location_label_refreshed_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("google_account_name", sa.String(length=255), nullable=True),
        sa.Column("google_location_name", sa.String(length=255), nullable=True),
        sa.Column("google_location_title", sa.String(length=300), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("connected_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'error', 'disconnected')",
            name="ck_integration_gbp_location_binding_status_enum",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_integration_gbp_location_binding_location_id"),
        "integration_gbp_location_binding",
        ["location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_gbp_location_binding_tenant_id"),
        "integration_gbp_location_binding",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_integration_gbp_binding_google_location_live",
        "integration_gbp_location_binding",
        ["google_location_name"],
        unique=True,
        postgresql_where=sa.text(_GOOGLE_LIVE),
        sqlite_where=sa.text(_GOOGLE_LIVE),
    )
    op.create_index(
        "uq_integration_gbp_binding_location_live",
        "integration_gbp_location_binding",
        ["location_id"],
        unique=True,
        postgresql_where=sa.text(_BINDING_LIVE),
        sqlite_where=sa.text(_BINDING_LIVE),
    )

    now = sa.func.now()
    provider = sa.table(
        "integration_provider",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("provider_code", sa.String()),
        sa.column("category", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("auth_type", sa.String()),
        sa.column("required_secret_slots", sa.JSON()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
        sa.column("docs_url", sa.String()),
        sa.column("supports_sandbox", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(provider.insert().values(id=uuid7(), version=1, created_at=now, updated_at=now, **PROVIDER_ROW))


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- the check for Google Business Profile secrets skipped: it reads rows and needs a live database")
    else:
        bind = op.get_bind()
        connections = bind.execute(
            sa.text(
                "SELECT count(*) FROM integration_connection c JOIN integration_provider p ON p.id = c.provider_id "
                "WHERE p.provider_code = :code"
            ),
            {"code": PROVIDER_CODE},
        ).scalar_one()
        bindings = bind.execute(
            sa.text(f"SELECT count(*) FROM integration_gbp_location_binding WHERE {_BINDING_LIVE}")
        ).scalar_one()
        if connections or bindings:
            raise RuntimeError(
                f"{connections} connection(s) to Google Business Profile and {bindings} live location binding(s) "
                "exist; they hold references to secrets this downgrade cannot reach. Disconnect the bindings, "
                "delete the connection (Integrations, platform scope) and downgrade again."
            )
    op.drop_index(
        "uq_integration_gbp_binding_location_live",
        table_name="integration_gbp_location_binding",
        postgresql_where=sa.text(_BINDING_LIVE),
        sqlite_where=sa.text(_BINDING_LIVE),
    )
    op.drop_index(
        "uq_integration_gbp_binding_google_location_live",
        table_name="integration_gbp_location_binding",
        postgresql_where=sa.text(_GOOGLE_LIVE),
        sqlite_where=sa.text(_GOOGLE_LIVE),
    )
    op.drop_index(
        op.f("ix_integration_gbp_location_binding_tenant_id"), table_name="integration_gbp_location_binding"
    )
    op.drop_index(
        op.f("ix_integration_gbp_location_binding_location_id"), table_name="integration_gbp_location_binding"
    )
    op.drop_table("integration_gbp_location_binding")
    op.drop_index(op.f("ix_integration_gbp_link_state_tenant_id"), table_name="integration_gbp_link_state")
    op.drop_table("integration_gbp_link_state")
    op.execute(sa.text("DELETE FROM integration_provider WHERE provider_code = :code").bindparams(code=PROVIDER_CODE))

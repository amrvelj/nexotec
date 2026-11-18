"""KAN-215 (D-A-28 as re-cut by Anto, 2026-11-14): customer mail leaves only
through the dealer's own Microsoft 365 or Google Workspace mailbox.

- `integration_provider`: `microsoft_365_mail` and `google_workspace_mail`,
  category `calendar_mail`, platform scope only (Nexotec's own app
  registration: config `clientId`, slot `client_secret`; Google's service
  account for domain-wide delegation: config `serviceAccountEmail`, slot
  `certificate`). No sandbox.
- `integration_mail_link_state`: one pending OAuth link (a hash of the state).
- `integration_sending_identity`: a connected mailbox, user's or Betrieb's.
- `integration_message_delivery`: one row per send.

The downgrade drops the three tables whatever they hold (no secret is kept in
them: a user grant's token is referenced by platform's connected-account
row, which stays). It refuses while a connection to a mailbox provider exists:
such a connection holds references to secret material the downgrade cannot
reach (the KAN-550 rule) — delete it first.

Revision ID: 3d8a3c0fb827
Revises: e7a2c91d4b60
Create Date: 2026-11-18 14:21:18.796938

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from app.core.types import GUID, UTCDateTime
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "3d8a3c0fb827"
down_revision: str | Sequence[str] | None = "e7a2c91d4b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (provider_code, display_name, required_secret_slots, required_config_keys, docs_url)
_PROVIDERS: tuple[tuple[str, str, list[str], list[str], str], ...] = (
    (
        "microsoft_365_mail",
        "Microsoft 365 (Mail)",
        ["client_secret"],
        ["clientId"],
        "https://learn.microsoft.com/graph/api/user-sendmail",
    ),
    (
        "google_workspace_mail",
        "Google Workspace (Gmail)",
        ["client_secret", "certificate"],
        ["clientId", "serviceAccountEmail"],
        "https://developers.google.com/gmail/api/reference/rest/v1/users.messages/send",
    ),
)
_PROVIDER_CODES = tuple(code for code, *_ in _PROVIDERS)


def upgrade() -> None:
    op.create_table(
        "integration_mail_link_state",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "tenant_id",
            GUID(),
            nullable=False,
            comment="Owned by the platform context (Dealership.id). No DB-level FK.",
        ),
        sa.Column(
            "user_id", GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("leg", sa.String(length=16), nullable=False),
        sa.Column("mailbox_address", sa.String(length=320), nullable=True),
        sa.Column("provider_tenant", sa.String(length=255), nullable=True),
        sa.Column("expires_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('user_mailbox', 'business_mailbox')", name="ck_integration_mail_link_state_kind_enum"
        ),
        sa.CheckConstraint(
            "leg IN ('user_signin', 'admin_signin', 'admin_consent')", name="ck_integration_mail_link_state_leg_enum"
        ),
        sa.CheckConstraint(
            "provider_code IN ('microsoft_365_mail', 'google_workspace_mail')",
            name="ck_integration_mail_link_state_provider_code_enum",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    op.create_index(
        op.f("ix_integration_mail_link_state_tenant_id"), "integration_mail_link_state", ["tenant_id"], unique=False
    )
    op.create_table(
        "integration_sending_identity",
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column(
            "owner_user_id", GUID(), nullable=True, comment="Owned by the platform context (User.id). No DB-level FK."
        ),
        sa.Column(
            "connected_account_id",
            GUID(),
            nullable=True,
            comment="Owned by the platform context (its connected account id, FR-AU-23). No DB-level FK.",
        ),
        sa.Column("mailbox_address", sa.String(length=320), nullable=False),
        sa.Column("external_account_id", sa.String(length=255), nullable=False),
        sa.Column("provider_tenant", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
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
            "kind IN ('user_mailbox', 'business_mailbox')", name="ck_integration_sending_identity_kind_enum"
        ),
        sa.CheckConstraint(
            "provider_code IN ('microsoft_365_mail', 'google_workspace_mail')",
            name="ck_integration_sending_identity_provider_code_enum",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'error', 'disconnected')", name="ck_integration_sending_identity_status_enum"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_integration_sending_identity_connected_account_id"),
        "integration_sending_identity",
        ["connected_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_sending_identity_owner_user_id"),
        "integration_sending_identity",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_sending_identity_provider_tenant"),
        "integration_sending_identity",
        ["provider_tenant"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_sending_identity_tenant_id"), "integration_sending_identity", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_integration_sending_identity_business_live",
        "integration_sending_identity",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'business_mailbox' AND status <> 'disconnected'"),
        sqlite_where=sa.text("kind = 'business_mailbox' AND status <> 'disconnected'"),
    )
    op.create_index(
        "uq_integration_sending_identity_user_live",
        "integration_sending_identity",
        ["tenant_id", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'user_mailbox' AND status <> 'disconnected'"),
        sqlite_where=sa.text("kind = 'user_mailbox' AND status <> 'disconnected'"),
    )
    op.create_table(
        "integration_message_delivery",
        sa.Column("sending_identity_id", GUID(), nullable=False),
        sa.Column("identity_kind", sa.String(length=24), nullable=False),
        sa.Column("sender_address", sa.String(length=320), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column(
            "document_id",
            GUID(),
            nullable=True,
            comment="Owned by the sending module (its document id). No DB-level FK.",
        ),
        sa.Column(
            "author_user_id", GUID(), nullable=False, comment="Owned by the platform context (User.id). No DB-level FK."
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("sent_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.CheckConstraint(
            "identity_kind IN ('user_mailbox', 'business_mailbox')",
            name="ck_integration_message_delivery_identity_kind_enum",
        ),
        sa.CheckConstraint("status IN ('sent', 'failed')", name="ck_integration_message_delivery_status_enum"),
        sa.ForeignKeyConstraint(
            ["sending_identity_id"],
            ["integration_sending_identity.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_integration_message_delivery_document_id"),
        "integration_message_delivery",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_message_delivery_sending_identity_id"),
        "integration_message_delivery",
        ["sending_identity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_message_delivery_tenant_id"), "integration_message_delivery", ["tenant_id"], unique=False
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
    for code, name, slots, config_keys, docs_url in _PROVIDERS:
        op.execute(
            provider.insert().values(
                id=uuid7(),
                version=1,
                provider_code=code,
                category="calendar_mail",
                display_name=name,
                auth_type="oauth2",
                required_secret_slots=slots,
                required_config_keys=config_keys,
                capability_codes=["mail_send"],
                docs_url=docs_url,
                supports_sandbox=False,
                created_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code in _PROVIDER_CODES)
    if context.is_offline_mode():
        op.execute("-- the check for mailbox-provider connections skipped: it reads rows and needs a live database")
    else:
        held = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT count(*) FROM integration_connection c JOIN integration_provider p ON p.id = c.provider_id "
                    f"WHERE p.provider_code IN ({codes})"
                )
            )
            .scalar_one()
        )
        if held:
            raise RuntimeError(
                f"{held} connection(s) to a mailbox provider exist; they hold references to secrets this "
                "downgrade cannot reach. Delete them (Integrations, platform scope) and downgrade again."
            )
    op.drop_index(op.f("ix_integration_message_delivery_tenant_id"), table_name="integration_message_delivery")
    op.drop_index(
        op.f("ix_integration_message_delivery_sending_identity_id"), table_name="integration_message_delivery"
    )
    op.drop_index(op.f("ix_integration_message_delivery_document_id"), table_name="integration_message_delivery")
    op.drop_table("integration_message_delivery")
    op.drop_index(
        "uq_integration_sending_identity_user_live",
        table_name="integration_sending_identity",
        postgresql_where=sa.text("kind = 'user_mailbox' AND status <> 'disconnected'"),
        sqlite_where=sa.text("kind = 'user_mailbox' AND status <> 'disconnected'"),
    )
    op.drop_index(
        "uq_integration_sending_identity_business_live",
        table_name="integration_sending_identity",
        postgresql_where=sa.text("kind = 'business_mailbox' AND status <> 'disconnected'"),
        sqlite_where=sa.text("kind = 'business_mailbox' AND status <> 'disconnected'"),
    )
    op.drop_index(op.f("ix_integration_sending_identity_tenant_id"), table_name="integration_sending_identity")
    op.drop_index(op.f("ix_integration_sending_identity_provider_tenant"), table_name="integration_sending_identity")
    op.drop_index(op.f("ix_integration_sending_identity_owner_user_id"), table_name="integration_sending_identity")
    op.drop_index(
        op.f("ix_integration_sending_identity_connected_account_id"), table_name="integration_sending_identity"
    )
    op.drop_table("integration_sending_identity")
    op.drop_index(op.f("ix_integration_mail_link_state_tenant_id"), table_name="integration_mail_link_state")
    op.drop_table("integration_mail_link_state")
    op.execute(f"DELETE FROM integration_provider WHERE provider_code IN ({codes})")

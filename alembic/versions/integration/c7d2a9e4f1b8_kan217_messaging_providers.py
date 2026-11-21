"""KAN-217 PR-3 (D-A-41): the Betrieb's own messaging providers — Twilio for
SMS and Meta's WhatsApp Cloud API for WhatsApp (Anto, 2026-11-14, Q2 B) — and
the `access_token` secret slot.

- `integration_provider`: `twilio` (config `accountSid`, `apiKeySid`,
  `sender`; slot `api_key`, the API key's secret) and `meta_whatsapp` (config
  `phoneNumberId`; slot `access_token`, a system user's token), category
  `messaging`, capability `messaging_send`. No sandbox: neither has a test
  mode that sends, and a Betrieb's connection is its own account (BYO).
- `integration_secret_ref.slot` gains `access_token`
  (`ck_integration_secret_ref_slot_enum`).

The downgrade refuses while a connection to either provider exists or an
`access_token` reference is held: it does not drop references to secret
material it cannot reach (the KAN-550 rule).

Revision ID: c7d2a9e4f1b8
Revises: d8e3f5a1c7b4
Create Date: 2026-11-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "c7d2a9e4f1b8"
down_revision: str | Sequence[str] | None = "d8e3f5a1c7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLOT_CHECK = "ck_integration_secret_ref_slot_enum"
# The model's member order (`SecretSlot`), as `enum_check_condition` words it.
_SLOTS_BEFORE = ("password", "client_secret", "refresh_token", "certificate", "api_key")
_SLOTS_AFTER = ("password", "client_secret", "refresh_token", "certificate", "api_key", "access_token")

# (provider_code, display_name, auth_type, secret slots, config keys, docs_url)
_PROVIDERS: tuple[tuple[str, str, str, list[str], list[str], str], ...] = (
    (
        "twilio",
        "Twilio (SMS)",
        "api_key",
        ["api_key"],
        ["accountSid", "apiKeySid", "sender"],
        "https://www.twilio.com/docs/messaging/api/message-resource",
    ),
    (
        "meta_whatsapp",
        "WhatsApp Business (Meta)",
        "bearer_token",
        ["access_token"],
        ["phoneNumberId"],
        "https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages",
    ),
)
_PROVIDER_CODES = tuple(code for code, *_ in _PROVIDERS)


def _condition(values: Sequence[str]) -> str:
    return '"slot" IN (' + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(_SLOT_CHECK, "integration_secret_ref", type_="check")
    op.create_check_constraint(_SLOT_CHECK, "integration_secret_ref", _condition(_SLOTS_AFTER))

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
    for code, name, auth_type, slots, config_keys, docs_url in _PROVIDERS:
        op.execute(
            provider.insert().values(
                id=uuid7(),
                version=1,
                provider_code=code,
                category="messaging",
                display_name=name,
                auth_type=auth_type,
                required_secret_slots=slots,
                required_config_keys=config_keys,
                capability_codes=["messaging_send"],
                docs_url=docs_url,
                supports_sandbox=False,
                created_at=now,
                updated_at=now,
            )
        )


def refuse_if_messaging_connections_exist(conn: sa.engine.Connection) -> None:
    codes = ", ".join(f"'{code}'" for code in _PROVIDER_CODES)
    held = conn.execute(
        sa.text(
            "SELECT count(*) FROM integration_connection c JOIN integration_provider p ON p.id = c.provider_id "
            f"WHERE p.provider_code IN ({codes})"
        )
    ).scalar_one()
    tokens = conn.execute(
        sa.text("SELECT count(*) FROM integration_secret_ref WHERE slot = 'access_token'")
    ).scalar_one()
    if held or tokens:
        raise RuntimeError(
            f"{held} connection(s) to a messaging provider and {tokens} 'access_token' secret reference(s) exist. "
            "Delete them on the release still running, then downgrade again: this migration does not drop "
            "references to secret material it cannot reach (KAN-550's rule, KAN-217)."
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-217 messaging connections check skipped: it reads rows and needs a live database")
    else:
        refuse_if_messaging_connections_exist(op.get_bind())
    codes = ", ".join(f"'{code}'" for code in _PROVIDER_CODES)
    op.execute(f"DELETE FROM integration_provider WHERE provider_code IN ({codes})")
    op.drop_constraint(_SLOT_CHECK, "integration_secret_ref", type_="check")
    op.create_check_constraint(_SLOT_CHECK, "integration_secret_ref", _condition(_SLOTS_BEFORE))

"""KAN-217 PR-5 (D-A-41, FR-A-51–54, ADR-204): `integration_message_delivery`
records every customer send, e-mail, SMS and WhatsApp.

- `channel` (every existing row is an e-mail: KAN-215 sent nothing else).
- An SMS or WhatsApp row names the Betrieb's provider connection
  (`connection_id`) and has no mailbox, sender address or subject, so those
  four become nullable; `ck_integration_message_delivery_channel_source`
  holds each channel to its own source.
- `body_text` (the text as sent; Q3 A, 2026-11-14), `document_link_id`
  (platform's secure link), `provider_status`, and the recipient in the
  three-column pattern (`customer_id`, `customer_label`,
  `customer_label_refreshed_at`).

The downgrade refuses while an SMS or WhatsApp row exists: it cannot be
expressed in the old table, and a delivery record is kept as long as the
document it sent (Q3 A), so it is not dropped silently.

Revision ID: 5e1b7c3a9d20
Revises: 5e1b7c9d3a20
Create Date: 2026-11-22 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from app.core.enum_type import enum_check_condition, enum_check_name
from app.core.types import GUID, UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "5e1b7c3a9d20"
down_revision: str | Sequence[str] | None = "5e1b7c9d3a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "integration_message_delivery"
_CHANNELS = ["email", "sms", "whatsapp"]
_SOURCE_CHECK = "ck_integration_message_delivery_channel_source"
_SOURCE_CONDITION = (
    "(channel = 'email' AND sending_identity_id IS NOT NULL AND identity_kind IS NOT NULL"
    " AND sender_address IS NOT NULL AND subject IS NOT NULL)"
    " OR (channel <> 'email' AND connection_id IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("channel", sa.String(length=16), nullable=True))
    op.execute(f"UPDATE {_TABLE} SET channel = 'email'")
    op.alter_column(_TABLE, "channel", existing_type=sa.String(length=16), nullable=False)
    op.create_check_constraint(enum_check_name(_TABLE, "channel"), _TABLE, enum_check_condition("channel", _CHANNELS))

    for name, type_ in (
        ("sending_identity_id", GUID()),
        ("identity_kind", sa.String(length=24)),
        ("sender_address", sa.String(length=320)),
        ("subject", sa.String(length=998)),
    ):
        op.alter_column(_TABLE, name, existing_type=type_, nullable=True)

    op.add_column(_TABLE, sa.Column("connection_id", GUID(), nullable=True))
    op.create_foreign_key(
        "integration_message_delivery_connection_id_fkey", _TABLE, "integration_connection", ["connection_id"], ["id"]
    )
    op.create_index(op.f("ix_integration_message_delivery_connection_id"), _TABLE, ["connection_id"], unique=False)
    op.add_column(_TABLE, sa.Column("body_text", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "document_link_id",
            GUID(),
            nullable=True,
            comment="Owned by the platform context (DocumentLink.id). No DB-level FK.",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "customer_id", GUID(), nullable=True, comment="Owned by the customer context (Customer.id). No DB-level FK."
        ),
    )
    op.create_index(op.f("ix_integration_message_delivery_customer_id"), _TABLE, ["customer_id"], unique=False)
    op.add_column(_TABLE, sa.Column("customer_label", sa.String(length=200), nullable=True))
    op.add_column(_TABLE, sa.Column("customer_label_refreshed_at", UTCDateTime(timezone=True), nullable=True))
    op.add_column(_TABLE, sa.Column("provider_status", sa.String(length=32), nullable=True))
    op.create_check_constraint(_SOURCE_CHECK, _TABLE, _SOURCE_CONDITION)


def downgrade() -> None:
    if not context.is_offline_mode():
        others = op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {_TABLE} WHERE channel <> 'email'"))
        if others:
            raise RuntimeError(
                f"{others} SMS or WhatsApp delivery record(s) exist; the old table cannot hold them and a delivery "
                "record is kept as long as its document (KAN-217, Q3 A). Export them before downgrading."
            )
    op.drop_constraint(_SOURCE_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "provider_status")
    op.drop_column(_TABLE, "customer_label_refreshed_at")
    op.drop_column(_TABLE, "customer_label")
    op.drop_index(op.f("ix_integration_message_delivery_customer_id"), table_name=_TABLE)
    op.drop_column(_TABLE, "customer_id")
    op.drop_column(_TABLE, "document_link_id")
    op.drop_column(_TABLE, "body_text")
    op.drop_index(op.f("ix_integration_message_delivery_connection_id"), table_name=_TABLE)
    op.drop_constraint("integration_message_delivery_connection_id_fkey", _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "connection_id")
    for name, type_ in (
        ("subject", sa.String(length=998)),
        ("sender_address", sa.String(length=320)),
        ("identity_kind", sa.String(length=24)),
        ("sending_identity_id", GUID()),
    ):
        op.alter_column(_TABLE, name, existing_type=type_, nullable=False)
    op.drop_constraint(enum_check_name(_TABLE, "channel"), _TABLE, type_="check")
    op.drop_column(_TABLE, "channel")

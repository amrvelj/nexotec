"""FR-17 / FR-18 customer fields — Phase B2 (KAN-50)

Fourteen stored fields ratified 2026-08-21 and the customer_tag child
table. The six derived fields (purchaseCount, lifetimeRevenue, openDeals,
vehicleCount, lastContactAt, serviceDue) are NOT here — they are Phase C,
behind the reporting projection (ADR-014).

Revision ID: 1f0a5c7d9e42
Revises: a1c46e02b7f9
Create Date: 2026-09-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1f0a5c7d9e42"
down_revision: Union[str, Sequence[str], None] = "a1c46e02b7f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GENDER = sa.Enum("FEMALE", "MALE", "OTHER", "UNSPECIFIED", name="gender", native_enum=False, length=16)
_PAYMENT_TERMS = sa.Enum(
    "PREPAYMENT", "NET_10", "NET_30", "NET_60", "ON_DELIVERY",
    name="paymentterms", native_enum=False, length=20,
)


def upgrade() -> None:
    # --- Identity (individual-only) -------------------------------------
    op.add_column("customer", sa.Column("title", sa.String(length=50), nullable=True))
    op.add_column(
        "customer",
        sa.Column("gender", _GENDER, nullable=False, server_default="UNSPECIFIED"),
    )

    # --- Contact ------------------------------------------------------------
    op.add_column("customer", sa.Column("website", sa.String(length=500), nullable=True))
    op.add_column(
        "customer",
        sa.Column("newsletter", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # --- Commercial standing ---------------------------------------------
    op.add_column("customer", sa.Column("payment_terms", _PAYMENT_TERMS, nullable=True))
    op.add_column("customer", sa.Column("credit_limit", sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column("customer", sa.Column("iban", sa.String(length=34), nullable=True))
    op.add_column(
        "customer",
        sa.Column("vat_registered", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # --- Relationship --------------------------------------------------------
    op.add_column(
        "customer",
        sa.Column(
            "advisor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the platform context (User). No DB-level FK (P-2).",
        ),
    )
    op.add_column("customer", sa.Column("advisor_label", sa.String(length=200), nullable=True))
    op.add_column("customer", sa.Column("advisor_label_refreshed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("customer", sa.Column("customer_since", sa.Date(), nullable=True))
    op.add_column("customer", sa.Column("next_follow_up", sa.Date(), nullable=True))
    op.add_column("customer", sa.Column("notes", sa.Text(), nullable=True))

    # --- Provenance --------------------------------------------------------
    op.add_column(
        "customer",
        sa.Column(
            "dealership_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Owned by the platform context (Dealership). No DB-level FK.",
        ),
    )

    op.create_index("ix_customer_advisor_id", "customer", ["advisor_id"])
    op.create_index("ix_customer_dealership_id", "customer", ["dealership_id"])

    # server_defaults existed only to backfill the NOT NULL columns on
    # existing rows — the ORM always supplies these on insert.
    op.alter_column("customer", "gender", server_default=None)
    op.alter_column("customer", "newsletter", server_default=None)
    op.alter_column("customer", "vat_registered", server_default=None)

    # --- customer_tag -----------------------------------------------------
    op.create_table(
        "customer_tag",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="Owned by the platform context (DealerGroup). No DB-level FK.",
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(length=60), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.UniqueConstraint("customer_id", "tag", name="uq_customer_tag_customer_id_tag"),
    )
    op.create_index("ix_customer_tag_customer_id", "customer_tag", ["customer_id"])
    op.create_index("ix_customer_tag_group_id", "customer_tag", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_tag_group_id", table_name="customer_tag")
    op.drop_index("ix_customer_tag_customer_id", table_name="customer_tag")
    op.drop_table("customer_tag")

    op.drop_index("ix_customer_dealership_id", table_name="customer")
    op.drop_index("ix_customer_advisor_id", table_name="customer")

    for column in (
        "dealership_id",
        "notes",
        "next_follow_up",
        "customer_since",
        "advisor_label_refreshed_at",
        "advisor_label",
        "advisor_id",
        "vat_registered",
        "iban",
        "credit_limit",
        "payment_terms",
        "newsletter",
        "website",
        "gender",
        "title",
    ):
        op.drop_column("customer", column)

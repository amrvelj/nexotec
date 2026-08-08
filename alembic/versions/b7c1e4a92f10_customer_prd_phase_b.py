"""Customer PRD Phase B: customer number, language, contact-model cleanup

Implements the blocking decisions from the Customer PRD v1.0 (Notion,
2026-08-08):

* D-01 `language` — the customer's correspondence language, mandatory.
* D-02 `customer_number` — per-tenant `K-000001` business key, immutable,
  allocated from the new `customer_number_sequence` table.
* D-03 / D-04 — the flat `email`/`phone` columns and
  `preferred_contact_method` are dropped. `customer_phone`/`customer_email`
  become the single source of truth and `preferred_channel` the only
  contact-preference field. This is a deliberate breaking change to the API
  contract, taken in one migration per Anto's ruling rather than phased.
* D-05 — the tenant-wide unique constraint on `email` is dropped. Family
  members and colleagues legitimately share an address.
* D-06 — indexes that make company_name / customer_number / phone / email
  searchable, so business customers can be found at all.
* D-11 — `address_postal_code` widened; it was 4 chars, which silently
  truncated every non-Swiss postal code.
* D-15 `salutation`.

**This migration is destructive and irreversible in practice.** Dropping
`email`/`phone` discards data that only partially exists elsewhere: rows
created before Phase A have no `customer_phone`/`customer_email`
counterpart. The pre-drop backfill below moves those values into the child
tables first, so nothing is lost — read that block before changing it.

Note on enum literals: SQLAlchemy persists PEP-435 enum *names*, not their
values, so every literal here is uppercase ('DE', 'MOBILE') to match what
the ORM writes. Lowercase literals would produce rows that violate the
column's own CHECK constraint.

Revision ID: b7c1e4a92f10
Revises: e9dd878c7836
Create Date: 2026-08-08
"""

import re
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7c1e4a92f10"
down_revision = "e9dd878c7836"
branch_labels = None
depends_on = None


def _normalise_phone(value):
    """Frozen copy of app.schemas.validators.normalise_phone.

    Deliberately duplicated rather than imported: a migration must keep
    doing what it did the day it ran, and importing application code makes
    an old migration's behaviour change whenever that helper is edited.
    """

    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return "41" + digits[1:]
    return digits


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "customer_number_sequence",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["dealer.id"]),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    # --- new columns, nullable until backfilled -------------------------
    op.add_column("customer", sa.Column("customer_number", sa.String(length=20), nullable=True))
    op.add_column(
        "customer",
        sa.Column(
            "language",
            sa.Enum("DE", "FR", "IT", "EN", name="language", native_enum=False, length=8),
            nullable=True,
        ),
    )
    op.add_column(
        "customer",
        sa.Column(
            "salutation",
            sa.Enum("HERR", "FRAU", "FIRMA", "NEUTRAL", name="salutation", native_enum=False, length=16),
            nullable=True,
        ),
    )
    op.add_column("customer_phone", sa.Column("phone_normalised", sa.String(length=20), nullable=True))

    # --- rescue flat contact data before dropping the columns -----------
    # Customers created before Phase A have their only phone/email in the
    # flat columns. Copy anything that is not already represented in the
    # child tables, marking it primary when the customer has no primary yet.
    existing_phones = {
        (r[0], r[1]) for r in conn.execute(sa.text("SELECT customer_id, phone_e164 FROM customer_phone"))
    }
    existing_emails = {
        (r[0], str(r[1]).lower())
        for r in conn.execute(sa.text("SELECT customer_id, email_address FROM customer_email"))
    }
    customers_with_primary_phone = {
        r[0] for r in conn.execute(sa.text("SELECT customer_id FROM customer_phone WHERE is_primary"))
    }
    customers_with_primary_email = {
        r[0] for r in conn.execute(sa.text("SELECT customer_id FROM customer_email WHERE is_primary"))
    }

    flat = conn.execute(
        sa.text("SELECT id, tenant_id, email, phone, created_at FROM customer ORDER BY tenant_id, created_at, id")
    ).fetchall()

    for row in flat:
        if row.phone and (row.id, row.phone) not in existing_phones:
            conn.execute(
                sa.text(
                    "INSERT INTO customer_phone"
                    " (id, tenant_id, customer_id, phone_type, phone_e164, phone_normalised, is_primary,"
                    "  created_at, updated_at)"
                    " VALUES (:id, :tenant_id, :customer_id, 'MOBILE', :phone, :normalised, :primary,"
                    "  :created_at, :created_at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": row.tenant_id,
                    "customer_id": row.id,
                    "phone": row.phone,
                    "normalised": _normalise_phone(row.phone),
                    "primary": row.id not in customers_with_primary_phone,
                    "created_at": row.created_at,
                },
            )
            customers_with_primary_phone.add(row.id)
        if row.email and (row.id, str(row.email).lower()) not in existing_emails:
            conn.execute(
                sa.text(
                    "INSERT INTO customer_email"
                    " (id, tenant_id, customer_id, email_type, email_address, is_primary,"
                    "  created_at, updated_at)"
                    " VALUES (:id, :tenant_id, :customer_id, 'PRIVATE', :email, :primary,"
                    "  :created_at, :created_at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": row.tenant_id,
                    "customer_id": row.id,
                    "email": row.email,
                    "primary": row.id not in customers_with_primary_email,
                    "created_at": row.created_at,
                },
            )
            customers_with_primary_email.add(row.id)

    # --- backfill the new columns ---------------------------------------
    # Row-by-row rather than a window-function UPDATE: portable across both
    # test lanes, and the row counts here are small. Revisit if a tenant
    # ever arrives with millions of customers to migrate.
    counters: dict = {}
    for row in flat:
        counters[row.tenant_id] = counters.get(row.tenant_id, 0) + 1
        conn.execute(
            sa.text("UPDATE customer SET customer_number = :num WHERE id = :id"),
            {"num": f"K-{counters[row.tenant_id]:06d}", "id": row.id},
        )
    for tenant_id, last in counters.items():
        conn.execute(
            sa.text("INSERT INTO customer_number_sequence (tenant_id, next_value) VALUES (:t, :n)"),
            {"t": tenant_id, "n": last + 1},
        )

    # German is the majority language across the Swiss dealer base and the
    # safest default for records that predate the field. Wrong for some
    # Romandie/Ticino customers — flagged to PM: worth a one-off correction
    # pass driven by address canton once D-09's postal dataset lands.
    conn.execute(sa.text("UPDATE customer SET language = 'DE' WHERE language IS NULL"))
    for row in conn.execute(sa.text("SELECT id, phone_e164 FROM customer_phone")):
        conn.execute(
            sa.text("UPDATE customer_phone SET phone_normalised = :n WHERE id = :id"),
            {"n": _normalise_phone(row.phone_e164), "id": row.id},
        )

    # --- tighten, index, and drop the old contract -----------------------
    with op.batch_alter_table("customer") as batch:
        batch.alter_column("customer_number", existing_type=sa.String(length=20), nullable=False)
        batch.alter_column(
            "language",
            existing_type=sa.Enum("DE", "FR", "IT", "EN", name="language", native_enum=False, length=8),
            nullable=False,
        )
        batch.alter_column(
            "address_postal_code",
            existing_type=sa.String(length=4),
            type_=sa.String(length=12),
            existing_nullable=True,
        )
        batch.drop_constraint("uq_customer_tenant_id_email", type_="unique")
        batch.create_unique_constraint("uq_customer_tenant_id_customer_number", ["tenant_id", "customer_number"])
        batch.drop_column("email")
        batch.drop_column("phone")
        batch.drop_column("preferred_contact_method")

    op.create_index("ix_customer_customer_number", "customer", ["customer_number"])
    op.create_index("ix_customer_company_name", "customer", ["company_name"])
    op.create_index("ix_customer_email_email_address", "customer_email", ["email_address"])

    with op.batch_alter_table("customer_phone") as batch:
        batch.alter_column("phone_normalised", existing_type=sa.String(length=20), nullable=False)
    op.create_index("ix_customer_phone_phone_normalised", "customer_phone", ["phone_normalised"])


def downgrade() -> None:
    """Restores the schema, not the data.

    The flat email/phone values are reconstructed from the primary child
    rows, which is lossy in the other direction: a customer with three phone
    numbers goes back to having one. Anything created after the upgrade with
    no primary contact simply comes back NULL.
    """

    conn = op.get_bind()

    op.drop_index("ix_customer_phone_phone_normalised", table_name="customer_phone")
    op.drop_column("customer_phone", "phone_normalised")

    op.drop_index("ix_customer_email_email_address", table_name="customer_email")
    op.drop_index("ix_customer_company_name", table_name="customer")
    op.drop_index("ix_customer_customer_number", table_name="customer")

    op.add_column("customer", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("customer", sa.Column("phone", sa.String(length=20), nullable=True))
    op.add_column(
        "customer",
        sa.Column(
            "preferred_contact_method",
            sa.Enum("EMAIL", "PHONE", "SMS", name="preferredcontactmethod", native_enum=False, length=16),
            nullable=True,
        ),
    )

    conn.execute(
        sa.text(
            "UPDATE customer SET email = ("
            " SELECT email_address FROM customer_email"
            " WHERE customer_email.customer_id = customer.id AND customer_email.is_primary LIMIT 1)"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE customer SET phone = ("
            " SELECT phone_e164 FROM customer_phone"
            " WHERE customer_phone.customer_id = customer.id AND customer_phone.is_primary LIMIT 1)"
        )
    )

    with op.batch_alter_table("customer") as batch:
        batch.drop_constraint("uq_customer_tenant_id_customer_number", type_="unique")
        batch.create_unique_constraint("uq_customer_tenant_id_email", ["tenant_id", "email"])
        batch.alter_column(
            "address_postal_code",
            existing_type=sa.String(length=12),
            type_=sa.String(length=4),
            existing_nullable=True,
        )
        batch.drop_column("salutation")
        batch.drop_column("language")
        batch.drop_column("customer_number")

    op.drop_table("customer_number_sequence")

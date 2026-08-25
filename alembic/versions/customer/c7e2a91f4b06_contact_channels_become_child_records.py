"""Contact channels become child records (WP-3 PR-5, ADR-067, Customers FR-07)

Three child tables carry the same six shared facts (type, label, isPrimary,
validFrom/validTo, doNotUse[+reason], consent[+source+timestamp]):
customer_phone and customer_email gain those six columns — they already
existed as child tables since Phase A (e9dd878c7836), this migration extends
them, it does not create them — and customer_address is new.

Chain note: the plan that scoped WP-3 originally expected this migration to
land between PR-2 (d4a1f6c8e3b7) and PR-4's legal_basis (f9b2e6a1c4d8), to
avoid touching customer_phone/customer_email twice. PR-4 shipped first and
its migration is already committed with down_revision pointing at PR-2 — and
legal_basis is a wholly separate, additive table that never touches
customer_phone/customer_email, so there is no actual second-pass conflict.
This migration heads the chain on top of the real, already-shipped head
(f9b2e6a1c4d8) rather than rewriting committed history to match the
originally-anticipated order.

Enum remapping — not a matter of taste, a fact about how the data is already
stored: SQLAlchemy's `Enum(..., native_enum=False)` persists the Python enum
MEMBER NAME, not its `.value`. Confirmed directly against this repo's
original create_table call (e9dd878c7836), which declares
`sa.Enum('PRIVATE', 'BUSINESS', name='emailtype', ...)` and
`sa.Enum('MOBILE', 'PRIVATE', 'OFFICE', name='phonetype', ...)` — those are
the uppercase member NAMES, not the lowercase JSON values the API exposes.
So the data UPDATE below rewrites those literal name strings:
  phone_type:  MOBILE unchanged, PRIVATE -> LANDLINE, OFFICE -> WORK
               (FAX is new — no existing row maps to it)
  email_type:  PRIVATE -> PERSONAL, BUSINESS -> WORK
               (INVOICING is new — no existing row maps to it)
is_primary itself is carried over completely unchanged. ADR-067 scopes
"exactly one primary" to (customer, type) rather than to the whole customer,
but every existing row already satisfies the old, STRICTER "at most one
primary per customer" rule — which is a subset of the new one, so no row can
violate it. Nothing to renormalize.

customer_address is backfilled from Customer's own flat address_* columns —
the one genuine flat-column-to-child-row move in this migration (phone/email
were already child tables, so there is no flat source for them to move
from). Verified two ways, per the brief's stronger bar for this PR: a
row-count match (one customer_address row for every customer with a
non-null address_street) AND a field-by-field equality check against the
source columns, which become read-only mirrors afterwards
(Customer.legacy_address_mirror) and are dropped in Phase C, not here.

Revision ID: c7e2a91f4b06
Revises: f9b2e6a1c4d8
Create Date: 2026-08-25 00:00:00.000000

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = 'c7e2a91f4b06'
down_revision: Union[str, Sequence[str], None] = 'f9b2e6a1c4d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PHONE_TYPE_REMAP_UP = {"PRIVATE": "LANDLINE", "OFFICE": "WORK"}
_EMAIL_TYPE_REMAP_UP = {"PRIVATE": "PERSONAL", "BUSINESS": "WORK"}


def _add_contact_channel_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("label", sa.String(length=60), nullable=True))
    op.add_column(table_name, sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table_name, sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table_name, sa.Column("do_not_use", sa.Boolean(), nullable=True))
    op.add_column(table_name, sa.Column("do_not_use_reason", sa.Text(), nullable=True))
    op.add_column(table_name, sa.Column("consent_granted", sa.Boolean(), nullable=True))
    op.add_column(table_name, sa.Column("consent_source", sa.String(length=100), nullable=True))
    op.add_column(table_name, sa.Column("consent_timestamp", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    table = sa.table(
        table_name,
        sa.column("valid_from", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("do_not_use", sa.Boolean()),
        sa.column("consent_granted", sa.Boolean()),
    )
    # valid_from backfills from created_at — the closest fact this schema
    # already has to "since when has this channel existed."
    bind.execute(table.update().values(valid_from=table.c.created_at, do_not_use=False, consent_granted=False))

    with op.batch_alter_table(table_name) as batch:
        batch.alter_column("valid_from", nullable=False)
        batch.alter_column("do_not_use", nullable=False)
        batch.alter_column("consent_granted", nullable=False)


def _drop_contact_channel_columns(table_name: str) -> None:
    for column in (
        "consent_timestamp",
        "consent_source",
        "consent_granted",
        "do_not_use_reason",
        "do_not_use",
        "valid_to",
        "valid_from",
        "label",
    ):
        op.drop_column(table_name, column)


def _remap_enum(table_name: str, column_name: str, mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    table = sa.table(table_name, sa.column(column_name, sa.String()))
    for old_name, new_name in mapping.items():
        result = bind.execute(
            table.update().where(getattr(table.c, column_name) == old_name).values(**{column_name: new_name})
        )
        print(
            f"MIGRATION_DRY_RUN_REPORT: {table_name}.{column_name} — {result.rowcount} row(s) "
            f"{old_name} -> {new_name}."
        )


def _customer_table():
    return sa.table(
        "customer",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
        sa.column("address_street", sa.String()),
        sa.column("address_house_number", sa.String()),
        sa.column("address_postal_code", sa.String()),
        sa.column("address_locality", sa.String()),
        sa.column("address_canton", sa.String()),
        sa.column("address_country", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("created_by", postgresql.UUID(as_uuid=True)),
        sa.column("updated_by", postgresql.UUID(as_uuid=True)),
    )


def _customer_address_table():
    return sa.table(
        "customer_address",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("group_id", postgresql.UUID(as_uuid=True)),
        sa.column("customer_id", postgresql.UUID(as_uuid=True)),
        sa.column("address_type", sa.String()),
        sa.column("address_street", sa.String()),
        sa.column("address_house_number", sa.String()),
        sa.column("address_postal_code", sa.String()),
        sa.column("address_locality", sa.String()),
        sa.column("address_canton", sa.String()),
        sa.column("address_country", sa.String()),
        sa.column("is_primary", sa.Boolean()),
        sa.column("valid_from", sa.DateTime(timezone=True)),
        sa.column("do_not_use", sa.Boolean()),
        sa.column("consent_granted", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("created_by", postgresql.UUID(as_uuid=True)),
        sa.column("updated_by", postgresql.UUID(as_uuid=True)),
    )


def _backfill_customer_addresses(bind) -> int:
    customer = _customer_table()
    rows = bind.execute(
        sa.select(customer).where(customer.c.address_street.is_not(None))
    ).fetchall()

    customer_address = _customer_address_table()
    for row in rows:
        bind.execute(
            customer_address.insert().values(
                id=uuid7(),
                group_id=row.group_id,
                customer_id=row.id,
                address_type="DOMICILE",
                address_street=row.address_street,
                address_house_number=row.address_house_number,
                address_postal_code=row.address_postal_code,
                address_locality=row.address_locality,
                address_canton=row.address_canton,
                address_country=row.address_country,
                is_primary=True,
                valid_from=row.created_at,
                do_not_use=False,
                consent_granted=False,
                created_at=row.created_at,
                updated_at=row.created_at,
                created_by=row.created_by,
                updated_by=row.updated_by,
            )
        )
    return len(rows)


def _verify_customer_address_backfill(bind, expected_count: int) -> None:
    customer_address = _customer_address_table()
    actual_count = bind.execute(
        sa.select(sa.func.count())
        .select_from(customer_address)
        .where(customer_address.c.address_type == "DOMICILE")
    ).scalar()
    if actual_count != expected_count:
        raise RuntimeError(
            f"WP-3 PR-5 backfill created {actual_count} customer_address row(s) but {expected_count} customer "
            "row(s) had a non-null address_street. Rolling back."
        )

    mismatches = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM customer c
            JOIN customer_address ca ON ca.customer_id = c.id AND ca.address_type = 'DOMICILE'
            WHERE c.address_street IS NOT NULL
              AND (
                ca.address_street IS DISTINCT FROM c.address_street
                OR ca.address_house_number IS DISTINCT FROM c.address_house_number
                OR ca.address_postal_code IS DISTINCT FROM c.address_postal_code
                OR ca.address_locality IS DISTINCT FROM c.address_locality
                OR ca.address_canton IS DISTINCT FROM c.address_canton
                OR ca.address_country IS DISTINCT FROM c.address_country
              )
            """
        )
    ).scalar()
    if mismatches:
        raise RuntimeError(
            f"WP-3 PR-5 backfill produced {mismatches} customer_address row(s) that do not exactly reproduce "
            "their source Customer.address_* columns. Rolling back."
        )
    print(
        f"MIGRATION_DRY_RUN_REPORT: customer_address — {expected_count} row(s) backfilled from Customer.address_*, "
        "0 count mismatches, 0 field mismatches (six-projection equivalence holds for the address projection)."
    )


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "customer_address",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False, comment=(
            "Owned by the platform context (DealerGroup). No DB-level FK."
        )),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "address_type",
            sa.Enum("DOMICILE", "BILLING", "DELIVERY", name="addresstype", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=60), nullable=True),
        sa.Column("address_street", sa.String(length=200), nullable=False),
        sa.Column("address_house_number", sa.String(length=20), nullable=False),
        sa.Column("address_postal_code", sa.String(length=12), nullable=False),
        sa.Column("address_locality", sa.String(length=100), nullable=False),
        sa.Column("address_canton", sa.String(length=2), nullable=True),
        sa.Column("address_country", sa.String(length=2), nullable=False, server_default="CH"),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("do_not_use", sa.Boolean(), nullable=False),
        sa.Column("do_not_use_reason", sa.Text(), nullable=True),
        sa.Column("consent_granted", sa.Boolean(), nullable=False),
        sa.Column("consent_source", sa.String(length=100), nullable=True),
        sa.Column("consent_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
    )
    op.create_index("ix_customer_address_customer_id", "customer_address", ["customer_id"])
    op.create_index("ix_customer_address_group_id", "customer_address", ["group_id"])

    _add_contact_channel_columns("customer_phone")
    _add_contact_channel_columns("customer_email")
    _remap_enum("customer_phone", "phone_type", _PHONE_TYPE_REMAP_UP)
    _remap_enum("customer_email", "email_type", _EMAIL_TYPE_REMAP_UP)

    expected_count = _backfill_customer_addresses(bind)
    _verify_customer_address_backfill(bind, expected_count)


def downgrade() -> None:
    op.drop_index("ix_customer_address_group_id", table_name="customer_address")
    op.drop_index("ix_customer_address_customer_id", table_name="customer_address")
    op.drop_table("customer_address")

    # FAX (phone) and INVOICING (email) have no pre-PR-5 representation —
    # rows of those types are left as-is; a downgrade cannot losslessly
    # reproduce a category the old schema never had.
    _remap_enum("customer_phone", "phone_type", {v: k for k, v in _PHONE_TYPE_REMAP_UP.items()})
    _remap_enum("customer_email", "email_type", {v: k for k, v in _EMAIL_TYPE_REMAP_UP.items()})
    _drop_contact_channel_columns("customer_email")
    _drop_contact_channel_columns("customer_phone")

"""Business master data: legal identity, Händlerschild, ASTRA, associations,
bank accounts, fiscal defaults (KAN-203; Dealer Administration PRD
"Dealership master data", D-A-22, D-A-25, D-A-27, FR-A-25)

- `dealership` gains the nullable legal-identity columns the PRD lists:
  `legal_form` (a code of the `legal_form` list), `commercial_register_office`,
  `hr_number`, `astra_operator_number`, `collective_vehicle_licences` (the
  Händlerschild list, JSON) and `association_memberships` (codes of the
  `association_membership` list, JSON).
- `dealership_bank_account`: the child collection (D-A-27), one default per
  dealership enforced by the partial unique index; IBAN checks live in the
  service, not the database.
- `dealership_settings`: one row per dealership with the shipped fiscal
  defaults — CHF, 0.05, 30 days, 01-01, KMU chart, month-end lock — and no
  `vat_method` (D-A-27). Backfilled for every existing dealership by pure
  SQL (INSERT … SELECT), so a new Betrieb and an old one read the same
  defaults; the backfilled rows carry a v4 id from gen_random_uuid(), the
  one place this table's ids are not UUIDv7 (as KAN-198's backfill did).
  No VAT rate and no bank details are seeded (FR-A-25).
- Reference lists `legal_form` and `association_membership`, seeded from
  app.platform.reference_data_seed (the same rows the tests use).

Downgrade drops the two tables, the six columns and the two lists.

Revision ID: e1f7a3c9b2d4
Revises: b4e8d2f6a1c3
Create Date: 2026-10-24 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.uuid7 import uuid7
from app.platform.reference_data_seed import BUSINESS_LISTS

# revision identifiers, used by Alembic.
revision: str = "e1f7a3c9b2d4"
down_revision: str | Sequence[str] | None = "b4e8d2f6a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording,
# tests/test_migrated_enum_checks.py).
_QR_REFERENCE_TYPES = ("qrr", "scor", "non")
_CHART_VARIANTS = ("kmu", "kaefer")
_PERIOD_LOCK_POLICIES = ("month_end_close", "quarter_end_close", "year_end_close")

_DEALERSHIP_COLUMNS = (
    ("legal_form", 32),
    ("commercial_register_office", 2),
    ("hr_number", 30),
    ("astra_operator_number", 30),
)


# Pure SQL so it renders offline; idempotent (tests/test_kan203_business_master_data.py).
BACKFILL_SQL = """
INSERT INTO dealership_settings (
    id, dealership_id, currency, cash_rounding, default_payment_terms_days,
    fiscal_year_start, accounting_system, mandant, chart_of_accounts_variant,
    period_lock_policy, version, created_at, updated_at, created_by, updated_by
)
SELECT gen_random_uuid(), d.id, 'CHF', 0.05, 30, '01-01', NULL, NULL, 'kmu',
       'month_end_close', 1, now(), now(), NULL, NULL
FROM dealership d
WHERE NOT EXISTS (SELECT 1 FROM dealership_settings s WHERE s.dealership_id = d.id)
"""


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    for name, length in _DEALERSHIP_COLUMNS:
        op.add_column("dealership", sa.Column(name, sa.String(length=length), nullable=True))
    op.add_column("dealership", sa.Column("collective_vehicle_licences", sa.JSON(), nullable=True))
    op.add_column("dealership", sa.Column("association_memberships", sa.JSON(), nullable=True))

    op.create_table(
        "dealership_bank_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("account_holder", sa.String(length=200), nullable=False),
        sa.Column("bank_name", sa.String(length=200), nullable=False),
        sa.Column("iban", sa.String(length=34), nullable=False),
        sa.Column("bic", sa.String(length=11), nullable=True),
        sa.Column("qr_iban", sa.String(length=34), nullable=True),
        sa.Column("qr_reference_type", sa.String(length=8), nullable=False),
        sa.Column("creditor_reference_prefix", sa.String(length=21), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            _check("qr_reference_type", _QR_REFERENCE_TYPES), name="ck_dealership_bank_account_qr_reference_type_enum"
        ),
    )
    op.create_index("ix_dealership_bank_account_dealership_id", "dealership_bank_account", ["dealership_id"])
    # Exactly one default per dealership (D-A-27).
    op.create_index(
        "uq_dealership_bank_account_default",
        "dealership_bank_account",
        ["dealership_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "dealership_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dealership_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dealership.id"), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cash_rounding", sa.DECIMAL(precision=4, scale=2), nullable=False),
        sa.Column("default_payment_terms_days", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_start", sa.String(length=5), nullable=False),
        sa.Column("accounting_system", sa.String(length=50), nullable=True),
        sa.Column("mandant", sa.String(length=50), nullable=True),
        sa.Column("chart_of_accounts_variant", sa.String(length=16), nullable=False),
        sa.Column("period_lock_policy", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("dealership_id", name="uq_dealership_settings_dealership_id"),
        sa.CheckConstraint(
            _check("chart_of_accounts_variant", _CHART_VARIANTS),
            name="ck_dealership_settings_chart_of_accounts_variant_enum",
        ),
        sa.CheckConstraint(
            _check("period_lock_policy", _PERIOD_LOCK_POLICIES), name="ck_dealership_settings_period_lock_policy_enum"
        ),
    )

    # The shipped defaults for every Betrieb that exists today (FR-A-25).
    op.execute(sa.text(BACKFILL_SQL))
    if not context.is_offline_mode():
        conn = op.get_bind()
        count = conn.execute(sa.text("SELECT count(*) FROM dealership_settings")).scalar_one()
        print(f"KAN-203: {count} dealership_settings row(s) after backfill")

    _seed_business_lists()


def _seed_business_lists() -> None:
    bind = op.get_bind()
    reference_list = sa.table(
        "reference_list",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_code", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    reference_value = sa.table(
        "reference_value",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("list_id", postgresql.UUID(as_uuid=True)),
        sa.column("value_code", sa.String()),
        sa.column("label_de", sa.String()),
        sa.column("label_fr", sa.String()),
        sa.column("label_it", sa.String()),
        sa.column("label_en", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = sa.func.now()
    for list_code, rows in BUSINESS_LISTS.items():
        list_id = uuid7()
        bind.execute(reference_list.insert().values(id=list_id, list_code=list_code, created_at=now, updated_at=now))
        for sort_order, (value_code, label_de, label_fr, label_it, label_en) in enumerate(rows):
            bind.execute(
                reference_value.insert().values(
                    id=uuid7(),
                    list_id=list_id,
                    value_code=value_code,
                    label_de=label_de,
                    label_fr=label_fr,
                    label_it=label_it,
                    label_en=label_en,
                    sort_order=sort_order,
                    active=True,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    for list_code in BUSINESS_LISTS:
        bind.execute(
            sa.text(
                "DELETE FROM reference_value WHERE list_id = (SELECT id FROM reference_list WHERE list_code = :code)"
            ).bindparams(code=list_code)
        )
        bind.execute(sa.text("DELETE FROM reference_list WHERE list_code = :code").bindparams(code=list_code))
    op.drop_table("dealership_settings")
    op.drop_index("uq_dealership_bank_account_default", table_name="dealership_bank_account")
    op.drop_index("ix_dealership_bank_account_dealership_id", table_name="dealership_bank_account")
    op.drop_table("dealership_bank_account")
    op.drop_column("dealership", "association_memberships")
    op.drop_column("dealership", "collective_vehicle_licences")
    for name, _ in reversed(_DEALERSHIP_COLUMNS):
        op.drop_column("dealership", name)

"""KAN-207 — number ranges (Dealer Administration PRD "Number ranges", D-A-17,
A-RULE-3, ADR-032; ADR-109)

One platform table, `number_range`: per Betrieb (the legal entity) per
document type one continuous sequence across all sites, its pattern, start
value, yearly reset, current value and freeze stamp.

Every Betrieb that exists gets its eleven ranges with the neutral seeds,
unfrozen (Betriebe already active are seeded unfrozen, KAN-207). The seed
patterns are written out literally — a migration never reads the
application's declaration (`app/platform/services/number_range.py::SEEDS`),
which moves on; `tests/test_number_ranges.py` keeps both equal. The offer
range's current value is not set here: this migration may not read Sales'
counter. `allocate_number` continues after it from the first offer on, and
`scripts/continue_offer_number_ranges.py` shows it on the screen before.

The seed is one INSERT … SELECT over `dealership` (no row is read into
Python), so it renders offline as plain SQL; only the report reads rows.

Revision ID: 2b478e186431
Revises: 994244560c8a
Create Date: 2026-11-02 14:53:18.733283

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "2b478e186431"
down_revision: str | Sequence[str] | None = "994244560c8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (document type, pattern, yearly reset)
_SEEDS = (
    ("offer", "O-{counter:6}", False),
    ("order_confirmation", "AB-{year}-{counter:5}", True),
    ("sales_contract", "C-{counter:6}", False),
    ("purchase_contract", "EV-{year}-{counter:5}", True),
    ("invoice", "R-{year}-{counter:6}", True),
    ("credit_note", "G-{year}-{counter:5}", True),
    ("dunning", "M-{year}-{counter:5}", True),
    ("service_order", "SA-{year}-{counter:5}", True),
    ("parts_order", "TB-{year}-{counter:5}", True),
    ("workshop_job", "WA-{year}-{counter:5}", True),
    ("stock_number", "S-{counter:6}", False),
)

_SEED_SQL = """
INSERT INTO number_range (
    id, tenant_id, document_type, pattern, start_value, yearly_reset, last_value, last_year, frozen_at,
    version, created_at, updated_at, created_by, updated_by
)
SELECT gen_random_uuid(), d.id, :document_type, :pattern, 1, :yearly_reset, NULL, NULL, NULL, 1, now(), now(), NULL, NULL
FROM dealership d
WHERE NOT EXISTS (SELECT 1 FROM number_range r WHERE r.tenant_id = d.id AND r.document_type = :document_type)
"""


def upgrade() -> None:
    op.create_table(
        "number_range",
        sa.Column("document_type", sa.String(length=24), nullable=False),
        sa.Column("pattern", sa.String(length=64), nullable=False),
        sa.Column("start_value", sa.Integer(), nullable=False),
        sa.Column("yearly_reset", sa.Boolean(), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=True),
        sa.Column("last_year", sa.Integer(), nullable=True),
        sa.Column("frozen_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.Column("tenant_id", app.core.types.GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", app.core.types.GUID(), nullable=True),
        sa.Column("updated_by", app.core.types.GUID(), nullable=True),
        sa.CheckConstraint(
            "document_type IN ('offer', 'order_confirmation', 'sales_contract', 'purchase_contract', 'invoice', "
            "'credit_note', 'dunning', 'service_order', 'parts_order', 'workshop_job', 'stock_number')",
            name="ck_number_range_document_type_enum",
        ),
        sa.CheckConstraint("last_value IS NULL OR last_value >= 0", name="ck_number_range_last_value_not_negative"),
        sa.CheckConstraint("start_value >= 1", name="ck_number_range_start_value_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_type", name="uq_number_range_tenant_id_document_type"),
    )
    op.create_index(op.f("ix_number_range_tenant_id"), "number_range", ["tenant_id"], unique=False)

    for document_type, pattern, yearly_reset in _SEEDS:
        op.execute(
            sa.text(_SEED_SQL).bindparams(document_type=document_type, pattern=pattern, yearly_reset=yearly_reset)
        )
    if context.is_offline_mode():
        op.execute("-- KAN-207 seed report skipped: it reads rows and needs a live database")
        return
    conn = op.get_bind()
    dealerships = conn.execute(sa.text("SELECT count(*) FROM dealership")).scalar_one()
    ranges = conn.execute(sa.text("SELECT count(*) FROM number_range")).scalar_one()
    print(f"KAN-207: {ranges} number ranges for {dealerships} Betriebe ({len(_SEEDS)} each), none frozen")


def downgrade() -> None:
    op.drop_index(op.f("ix_number_range_tenant_id"), table_name="number_range")
    op.drop_table("number_range")

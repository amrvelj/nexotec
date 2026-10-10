"""sales_contract carries its own pricing inputs (KAN-67, ADR-083)

A pending contract becomes editable — vehicle, price build-up and trade-in —
through the offer's own snapshot and build-up code. That code reads its
INPUTS (the frozen vehicle snapshot, a manually entered base price, the
overall discount and the line items), which until now only the offer
stored; the contract kept the derived figures alone. This adds the inputs
to `sales_contract`, with the same names and meaning as on `sales_offer`,
plus `total_before_discount` and `cost_basis`, the two derived figures the
contract did not yet keep. Line items already have a `contract_id` column.

Backfill — pending contracts created from an offer only. Their inputs and
line items are copied from that offer, but only where the offer still
yields the contract's own figures (same vehicle, same base, options,
accessories, discount and price): an offer edited after its contract was
created no longer describes that contract, and copying it would silently
change the contract's price on its first edit. Such a contract keeps no
inputs and its figures as they are: it is not editable (409
`contract_priced_without_inputs`, services/contract_completeness.py) but can
still be confirmed or cancelled. Confirmed,
cancelled and invoiced contracts are frozen and get nothing; a contract
created directly (no offer) has nothing to copy. Writes no outbox rows.
Re-runnable: a contract that already has a snapshot or line items is
skipped.

`status` is a StoredEnum mid-way through KAN-86, so both the member name
(`PENDING`) and the value (`pending`) are matched.

Downgrade drops the columns and deletes every line item that belongs to a
contract — the only rows that carry a `contract_id` are the ones this
change writes. Not lossless: a pending contract's own edits are gone.

Revision ID: 381b0c2c67dc
Revises: 63b0d0b20fab
Create Date: 2026-10-10 18:40:21.812195

"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.core.types
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = '381b0c2c67dc'
down_revision: Union[str, Sequence[str], None] = '63b0d0b20fab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

_PENDING = ("pending", "PENDING")
# What must agree between a contract and its offer before the offer's inputs
# may be copied: the vehicle, and every figure the build-up derives.
_MUST_MATCH = (
    "vehicle_source",
    "stock_item_id",
    "vehicle_label",
    "configuration_id",
    "base_price",
    "options_total",
    "accessories_total",
    "discount_amount",
    "gross_price",
)
_INPUTS = (
    "manual_base_price",
    "vehicle_snapshot",
    "vehicle_snapshot_frozen_at",
    "discount_type",
    "discount_value",
    "total_before_discount",
    "cost_basis",
)
_LINE_ITEM_COLUMNS = (
    "kind",
    "code",
    "label",
    "unit_price",
    "quantity",
    "included",
    "discount_type",
    "discount_value",
    "discount_resolved_amount",
    "discount_suppressed_reason",
    "position",
)

_uuid = postgresql.UUID(as_uuid=True)
_contract = sa.table(
    "sales_contract",
    sa.column("id", _uuid),
    sa.column("tenant_id", _uuid),
    sa.column("offer_id", _uuid),
    sa.column("status", sa.String),
    *(sa.column(name) for name in _MUST_MATCH if name not in ("stock_item_id", "configuration_id")),
    sa.column("stock_item_id", _uuid),
    sa.column("configuration_id", _uuid),
    sa.column("vehicle_snapshot", sa.JSON),
    *(sa.column(name) for name in _INPUTS if name != "vehicle_snapshot"),
)
_offer = sa.table(
    "sales_offer",
    sa.column("id", _uuid),
    sa.column("tenant_id", _uuid),
    *(sa.column(name) for name in _MUST_MATCH if name not in ("stock_item_id", "configuration_id")),
    sa.column("stock_item_id", _uuid),
    sa.column("configuration_id", _uuid),
    sa.column("vehicle_snapshot", sa.JSON),
    *(sa.column(name) for name in _INPUTS if name != "vehicle_snapshot"),
)
_line_item = sa.table(
    "sales_line_item",
    sa.column("id", _uuid),
    sa.column("tenant_id", _uuid),
    sa.column("offer_id", _uuid),
    sa.column("contract_id", _uuid),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    *(sa.column(name) for name in _LINE_ITEM_COLUMNS),
)


def backfill(conn: sa.Connection) -> tuple[int, int]:
    """Returns (copied, skipped because the offer no longer matches)."""

    has_line_items = {
        row.contract_id
        for row in conn.execute(sa.select(_line_item.c.contract_id).where(_line_item.c.contract_id.is_not(None)))
    }
    offer_table = _offer.alias("o")
    pairs = conn.execute(
        sa.select(*_contract.c, *offer_table.c)
        .select_from(
            _contract.join(
                offer_table,
                sa.and_(offer_table.c.id == _contract.c.offer_id, offer_table.c.tenant_id == _contract.c.tenant_id),
            )
        )
        .where(_contract.c.status.in_(_PENDING), _contract.c.vehicle_snapshot.is_(None))
    ).all()

    contract_names = [c.name for c in _contract.c]
    offer_names = [c.name for c in offer_table.c]
    copied = skipped = 0
    now = sa.func.now()
    for row in pairs:
        # Both tables share column names; split the row by position.
        contract = dict(zip(contract_names, row[: len(contract_names)]))
        offer = dict(zip(offer_names, row[len(contract_names) :]))
        if contract["id"] in has_line_items:
            continue
        if any(contract[name] != offer[name] for name in _MUST_MATCH):
            skipped += 1
            continue
        conn.execute(
            sa.update(_contract)
            .where(_contract.c.id == contract["id"])
            .values({name: offer[name] for name in _INPUTS})
        )
        items = conn.execute(
            sa.select(*(_line_item.c[name] for name in _LINE_ITEM_COLUMNS)).where(
                _line_item.c.offer_id == offer["id"], _line_item.c.tenant_id == offer["tenant_id"]
            )
        ).all()
        if items:
            conn.execute(
                sa.insert(_line_item).values(created_at=now, updated_at=now),
                [
                    {
                        "id": uuid7(),
                        "tenant_id": contract["tenant_id"],
                        "offer_id": None,
                        "contract_id": contract["id"],
                        **dict(zip(_LINE_ITEM_COLUMNS, item)),
                    }
                    for item in items
                ],
            )
        copied += 1
    return copied, skipped


def upgrade() -> None:
    op.add_column('sales_contract', sa.Column('manual_base_price', sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column('sales_contract', sa.Column('vehicle_snapshot', sa.JSON(), nullable=True))
    op.add_column(
        'sales_contract', sa.Column('vehicle_snapshot_frozen_at', app.core.types.UTCDateTime(timezone=True), nullable=True)
    )
    op.add_column('sales_contract', sa.Column('discount_type', sa.String(length=16), nullable=True))
    op.add_column('sales_contract', sa.Column('discount_value', sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column('sales_contract', sa.Column('total_before_discount', sa.DECIMAL(precision=12, scale=2), nullable=True))
    op.add_column('sales_contract', sa.Column('cost_basis', sa.DECIMAL(precision=12, scale=2), nullable=True))
    copied, skipped = backfill(op.get_bind())
    logger.info(
        "KAN-67 backfill: %d pending contract(s) took their offer's pricing inputs; %d skipped because the offer "
        "no longer yields the contract's figures",
        copied,
        skipped,
    )


def downgrade() -> None:
    op.get_bind().execute(sa.delete(_line_item).where(_line_item.c.contract_id.is_not(None)))
    op.drop_column('sales_contract', 'cost_basis')
    op.drop_column('sales_contract', 'total_before_discount')
    op.drop_column('sales_contract', 'discount_value')
    op.drop_column('sales_contract', 'discount_type')
    op.drop_column('sales_contract', 'vehicle_snapshot_frozen_at')
    op.drop_column('sales_contract', 'vehicle_snapshot')
    op.drop_column('sales_contract', 'manual_base_price')

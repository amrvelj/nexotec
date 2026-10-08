"""One-way migration off the shipped `transaction` table (WP-8 PR-7,
ADR-050, KAN-26). Idempotent and re-runnable: a `sale` row is keyed on
`SalesContract.legacy_transaction_id` (see the model comment there), a
`trade_in` row on `StockItem.pipeline_ref` (see below). A row already
migrated is reported and skipped, never migrated twice.

MANDATORY DRY RUN. Default mode is dry-run — it produces the row-level
report below and writes NOTHING. Nothing commits until a human has read
that report and re-runs with --commit, same convention as
migrate_legacy_vehicles.py.

SCOPE — only `status = completed` rows migrate. A `draft` or `cancelled`
transaction never became a real sale; migrating one into a confirmed
contract would fabricate a deal that never closed. Reported as
`not_completed`, never touched. A completed row missing `amount` or
`transaction_date` is ALSO never touched (`amount_missing` /
`transaction_date_missing`) — neither is a DB constraint, only a
service-layer convention (see the model comments), so a historically
dirty legacy row can violate it; both fields feed real fiscal/lifecycle
data (sale price, purchase price and the fiktiver Vorsteuerabzug off
`amount`; in_stock_at, purchase_date, left_stock_at and this migration's
own processing order off `transaction_date`) and neither is guessed at
if absent — `amount or Decimal(0)` would have silently fabricated a
CHF 0 price, which is exactly the failure mode this migration exists to
avoid.

NO OUTBOX EVENTS. This is a one-time historical backfill, not a new
business event — publishing sales.contract.confirmed /
inventory.stock_item.purchased etc. for a sale that happened years ago
would make live consumers (a real stock reservation, a real pipeline
auto-create) treat old history as something happening today. Rows are
written directly via the ORM, same as migrate_legacy_vehicles.py's own
precedent (that script publishes nothing either). Because Sales learns a
purchase only from inventory.stock_item.purchased, a trade-in's booked
purchase is also written straight into Sales' replica (KAN-100).

`sale` -> a confirmed sales_contract with a SYNTHESISED offer (ADR-050's
own word) AND a StockItem for the sold vehicle — NOT a plain-text
vehicle_label. The product owner was explicit that a migrated contract
needs the same concrete, joinable stock_item_id link every other
contract gets, and the same real frozen vehicle_snapshot (via
freeze_vehicle_snapshot / apply_build_up — the actual production code
path, not a hand-rolled duplicate), not a string description.

`trade_in` -> a StockItem carrying the acquisition (2026-09-17 revision
— this was previously rejected unconditionally; both cited blockers
turned out to be stale, see below).

ONE STOCK ITEM PER STAY IN STOCK, AS LIVE STOCK DOES (KAN-111, KAN-256).
A VIN is in a dealership's stock at most once
(`uq_stock_item_tenant_id_vin_in_stock`, over items with left_stock_at
NULL); a car that left stock and comes back is a NEW item, and the sold
one stays as history. Legacy history follows the same rule (Anto,
2026-10-08): a car the old system shows sold and later traded back in
gets a second stock item, and the sold item is never reopened — its
left_stock_at, sale price and contract, and any purchase it carried,
stay exactly as they were. Rows are processed by transaction_date, with
Transaction.id as a tiebreak, so a trade-in is seen before its own later
resale. For each row, `_stock_items_for_vin` resolves the VIN
deterministically: the one item still in stock (the index allows at
most one), and the most recently sold one (latest left_stock_at, then
id). Then:
  - a `trade_in` row with no item in stock creates a NEW item carrying
    the acquisition (supplier_name, supplier_is_vat_registered,
    purchase_date, purchase_price, notional_input_tax_*), whether or not
    the VIN was sold before.
  - a `sale` row closes out the item in stock (the stay an earlier
    trade-in opened): left_stock_at + base/list/effective_price, the
    acquisition left untouched. With no item in stock and none sold, it
    creates the item it sells; a sale is definitionally the vehicle
    leaving stock at that price, however it got there.
Two genuine anomalies are reported rather than silently overwritten,
since a migration tool should never guess which of two conflicting
historical facts is right: a `trade_in` row for a car this import
already has in stock (`vehicle_already_in_stock` — two trade-ins with no
intervening sale), and a `sale` row for a car with no item in stock but
one already sold (`vehicle_already_in_stock` — two sales with no
intervening trade-in). Both are left untouched and flagged for a human.

NEVER A CAR LIVE STOCK KNOWS. Historical data is imported before a
dealership starts to work (Anto, 2026-10-08), so a stock item this
import did not create — live Stock's, in stock or already sold — can
only meet it by mistake. This import's own items are its trade-ins
(`pipeline_ref = legacy-transaction:<id>`) and the items its sales
created (named by a contract carrying `legacy_transaction_id`); any
other item for the VIN is live. A row for such a car is reported
(`vehicle_known_to_live_stock`) and changes nothing: a sale would close
out a car in stock today, and a trade-in would write a years-old
purchase onto it — or, for a car live Stock already sold, create a
phantom item in stock for a car the dealership no longer has.

WHY THE TRADE-IN PATH WAS PREVIOUSLY BLOCKED, AND WHY IT NO LONGER IS
The original rejection (see git history / Notion KAN-26) cited two
missing inputs. Re-verified 2026-09-17, both are stale:
- Vehicle condition IS recoverable — Transaction.vehicle_id resolves to
  a legacy Vehicle exactly the same way it does for a `sale` row; the
  original claim addressed the wrong data source (a trade-in's own
  Transaction row has no condition column, true, but it was never the
  right place to look — the linked vehicle always was).
- supplier_is_vat_registered now has a real source: Customer.vat_registered,
  added by KAN-50 after this rejection was written. The customer
  trading in the vehicle IS the acquisition's supplier.
Product owner's own ruling stands and is still honoured: a genuinely
absent value is reported, never guessed. What changed is that these two
values are no longer genuinely absent.

Per-row outcome, in the report:
- `not_completed`          — status was draft or cancelled. Never touched.
- `amount_missing`         — a completed row with no `amount`. Reported,
  never defaulted to zero (a zero fiscal figure is a real, different
  fact from an absent one).
- `transaction_date_missing` — a completed row with no `transaction_date`.
  Reported, never guessed — this field drives in_stock_at, purchase_date,
  left_stock_at and this migration's own processing order.
- `vehicle_unresolved`     — Transaction.vehicle_id is a legacy vehicle.id
  with no vehicle_mdm row carrying it as migrated_from_legacy_vehicle_id
  (scripts/migrate_legacy_vehicles.py never ran for it, or it was
  rejected there). REPORTED, never guessed at.
- `customer_unresolved`    — Transaction.customer_id does not resolve to an
  existing Customer row.
- `dealership_unresolved`  — Transaction.tenant_id (a trade-in only needs
  this, to read the dealer's vat_rate) has no Dealership row — plausible
  for old/decommissioned legacy tenant data, since there is no DB-level
  FK enforcing it (see Dealership's own comment on TenantScopedMixin).
- `vehicle_already_in_stock` — a genuine anomaly: two rows of the SAME
  type for one VIN with no intervening row of the other type between
  them (see above). Reported, not overwritten.
- `vehicle_known_to_live_stock` — the VIN has an item live Stock
  created, in stock or sold, not this import (see NEVER A CAR LIVE STOCK
  KNOWS). Reported; nothing is written.
- `condition_lossy`        — the legacy Vehicle's own condition was
  certified_pre_owned, which StockItemCondition has no value for;
  approximated as USED (matches migrate_legacy_vehicles.py's own
  totaled->scrapped precedent: flagged, not silent).
- `migrated`                — sale: a sales_contract + synthesised
  sales_offer were created, against a StockItem (new, or the one an
  earlier trade-in opened). trade_in: a new StockItem was created
  carrying the acquisition. (Or would be, in dry-run — dry-run does not
  flush, so it cannot detect a same-pass vehicle_already_in_stock/reuse
  interaction between two rows that would only become visible to each
  other on a real commit; a real --commit run is always the
  authoritative report for that specific interaction.)
- `already_migrated`       — this transaction id is already stamped on a
  sales_contract (sale) or a stock_item.pipeline_ref (trade_in). A
  re-run reports every row here and changes nothing.

Usage:
    DMS_DATABASE_URL=... python scripts/migrate_transaction_rows.py            # dry run (default)
    DMS_DATABASE_URL=... python scripts/migrate_transaction_rows.py --commit   # writes, after you've read the report
"""

import argparse
import dataclasses
import sys
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models.customer import Customer, CustomerType
from app.db import SessionLocal
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.services.stock_item import allocate_stock_number
from app.platform.models.dealership import Dealership
from app.sales.models.contract import ContractStatus, FinancingKind, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.sales.services.numbering import allocate_contract_number, allocate_offer_number
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot
from app.sales.services.stock_item_purchase import record_stock_item_purchased
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition as LegacyVehicleCondition
from app.vehicle.models.vehicle_mdm import VehicleMdm

_VEHICLE_ALREADY_IN_STOCK_NOTE = (
    "stock_item {item_id} for vehicle_mdm {mdm_id} (vin={vin}) already carries {conflict} — a second {row_type} "
    "row for the same vehicle with no intervening row of the other type between them is a genuine anomaly this "
    "migration does not resolve on your behalf, reported rather than silently overwritten"
)

_VEHICLE_KNOWN_TO_LIVE_STOCK_NOTE = (
    "stock_item {item_id} (stock number {stock_number}, {state}) for vehicle_mdm {mdm_id} (vin={vin}) was not "
    "created by this import — live Stock knows the car. History is imported before a dealership starts to work, "
    "so this {row_type} row is reported and nothing is written"
)

_LEGACY_PIPELINE_REF_PREFIX = "legacy-transaction:"

_CONDITION_MAP: dict[LegacyVehicleCondition, StockItemCondition] = {
    LegacyVehicleCondition.NEW: StockItemCondition.NEW,
    LegacyVehicleCondition.USED: StockItemCondition.USED,
    LegacyVehicleCondition.DEMO: StockItemCondition.DEMO,
    # No StockItemCondition equivalent — approximated, always flagged
    # (condition_lossy), never silent. Same posture as migrate_legacy_
    # vehicles.py's totaled -> scrapped approximation.
    LegacyVehicleCondition.CERTIFIED_PRE_OWNED: StockItemCondition.USED,
}


@dataclasses.dataclass
class RowOutcome:
    transaction_id: uuid.UUID
    transaction_type: str
    outcome: str
    notes: str = ""
    new_contract_id: uuid.UUID | None = None
    new_stock_item_id: uuid.UUID | None = None


@dataclasses.dataclass
class MigrationReport:
    committed: bool
    total_rows: int
    outcomes: list[RowOutcome]
    aborted: bool = False

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for row in self.outcomes:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        lines = [
            f"migrate_transaction_rows: {'COMMITTED' if self.committed else 'DRY RUN (nothing written)'}"
            + (" — ABORTED" if self.aborted else ""),
            f"  total transaction rows examined: {self.total_rows}",
        ]
        for outcome, count in sorted(counts.items()):
            lines.append(f"  {outcome}: {count}")
        for row in self.outcomes:
            if row.outcome in (
                "vehicle_unresolved", "customer_unresolved", "dealership_unresolved", "vehicle_already_in_stock",
                "vehicle_known_to_live_stock", "condition_lossy", "amount_missing", "transaction_date_missing", "error",
            ):
                lines.append(f"    {row.outcome.upper()} {row.transaction_type} {row.transaction_id}: {row.notes}")
        return "\n".join(lines)


def _resolve_vehicle_mdm(db: Session, legacy_vehicle_id: uuid.UUID) -> VehicleMdm | None:
    return db.scalar(select(VehicleMdm).where(VehicleMdm.migrated_from_legacy_vehicle_id == legacy_vehicle_id))


@dataclasses.dataclass(frozen=True)
class _VinStockItems:
    in_stock: StockItem | None
    latest_sold: StockItem | None


def _stock_items_for_vin(db: Session, tenant_id: uuid.UUID, vin: str | None) -> _VinStockItems:
    """The VIN's item still in stock — at most one, by
    uq_stock_item_tenant_id_vin_in_stock — and its most recently sold item
    (latest left_stock_at, then id), so a VIN with a sold row and a
    returned one never resolves to an arbitrary row (KAN-256).
    """

    if vin is None:
        return _VinStockItems(in_stock=None, latest_sold=None)
    of_vin = select(StockItem).where(StockItem.tenant_id == tenant_id, StockItem.vin == vin)
    in_stock = db.scalars(of_vin.where(StockItem.left_stock_at.is_(None))).one_or_none()
    latest_sold = db.scalars(
        of_vin.where(StockItem.left_stock_at.is_not(None))
        .order_by(StockItem.left_stock_at.desc(), StockItem.id.desc())
        .limit(1)
    ).one_or_none()
    return _VinStockItems(in_stock=in_stock, latest_sold=latest_sold)


def _live_stock_item_for_vin(db: Session, tenant_id: uuid.UUID, vin: str | None) -> StockItem | None:
    """An item for this VIN that this import did not create — the one in
    stock first, else the most recently sold — or None. This import's own
    items are its trade-ins (marked by pipeline_ref) and the items its
    sales created (named by a legacy contract). Two queries, one per
    context, rather than a join across Stock's and Sales' tables.
    """

    if vin is None:
        return None
    items = db.scalars(
        select(StockItem)
        .where(StockItem.tenant_id == tenant_id, StockItem.vin == vin)
        .order_by(StockItem.left_stock_at.desc().nulls_first(), StockItem.id.desc())
    ).all()
    if not items:
        return None
    sold_by_this_import = set(
        db.scalars(
            select(SalesContract.stock_item_id).where(
                SalesContract.tenant_id == tenant_id,
                SalesContract.legacy_transaction_id.is_not(None),
                SalesContract.stock_item_id.in_([item.id for item in items]),
            )
        ).all()
    )
    for item in items:
        traded_in_by_this_import = item.pipeline_ref is not None and item.pipeline_ref.startswith(
            _LEGACY_PIPELINE_REF_PREFIX
        )
        if not traded_in_by_this_import and item.id not in sold_by_this_import:
            return item
    return None


def _vehicle_known_to_live_stock(
    txn: Transaction, row_type: str, item: StockItem, vehicle_mdm: VehicleMdm,
) -> RowOutcome:
    state = "in stock" if item.left_stock_at is None else f"left stock {item.left_stock_at}"
    return RowOutcome(
        txn.id, row_type, "vehicle_known_to_live_stock",
        _VEHICLE_KNOWN_TO_LIVE_STOCK_NOTE.format(
            item_id=item.id, stock_number=item.stock_number, state=state, mdm_id=vehicle_mdm.id, vin=vehicle_mdm.vin,
            row_type=row_type,
        ),
    )


def _create_stock_item(
    db: Session, *, tenant_id: uuid.UUID, vehicle_mdm: VehicleMdm, legacy_vehicle: LegacyVehicle,
    condition: StockItemCondition,
) -> StockItem:
    """A new item, in stock. Callers create one only when the VIN has no
    item in stock, so the in-stock VIN index never refuses it."""

    item = StockItem(
        tenant_id=tenant_id,
        stock_number=allocate_stock_number(db, tenant_id),
        vehicle_id=vehicle_mdm.id,
        vin=vehicle_mdm.vin,
        vehicle_label=_vehicle_label(legacy_vehicle),
        condition=condition,
        lifecycle_status=LifecycleStatus.IN_STOCK,
        created_by=None,
        updated_by=None,
    )
    db.add(item)
    db.flush()
    return item


def _vehicle_label(legacy: LegacyVehicle) -> str:
    parts = [legacy.make, legacy.model]
    if legacy.trim:
        parts.append(legacy.trim)
    return " ".join(parts) + f" ({legacy.model_year})"


def _customer_label(customer: Customer) -> str | None:
    if customer.customer_type == CustomerType.BUSINESS:
        return customer.company_name
    if customer.first_name or customer.last_name:
        return f"{customer.first_name or ''} {customer.last_name or ''}".strip()
    return None


def _compute_notional_input_tax(*, purchase_price: Decimal, rate: Decimal | None) -> Decimal | None:
    """Art. 28a MWSTG: amount = rate / (100 + rate) * purchase price. The
    same reverse-inclusive formula app.inventory.services.purchase's own
    _compute_notional_input_tax uses (a different figure, same math) —
    duplicated locally rather than imported, matching the established,
    deliberate precedent for reusing this exact formula cross-context:
    app.sales.services.document::_compute_vat_amount duplicates it too,
    citing the source in its own docstring, rather than importing a
    leading-underscore (author-declared-private) symbol from another
    bounded context's service module. None when the dealership hasn't
    configured a vat_rate yet — applicable stays recorded, the amount is
    simply not computable until it does.
    """

    if rate is None:
        return None
    return (purchase_price * rate / (Decimal(100) + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _migrate_sale(db: Session, txn: Transaction, *, commit: bool) -> RowOutcome:
    legacy_vehicle = db.get(LegacyVehicle, txn.vehicle_id)
    if legacy_vehicle is None:
        return RowOutcome(txn.id, "sale", "vehicle_unresolved", f"legacy vehicle {txn.vehicle_id} does not exist")
    vehicle_mdm = _resolve_vehicle_mdm(db, legacy_vehicle.id)
    if vehicle_mdm is None:
        return RowOutcome(
            txn.id, "sale", "vehicle_unresolved",
            f"legacy vehicle {legacy_vehicle.id} has no vehicle_mdm row carrying it as "
            "migrated_from_legacy_vehicle_id — run scripts/migrate_legacy_vehicles.py first",
        )
    customer = db.get(Customer, txn.customer_id)
    if customer is None:
        return RowOutcome(txn.id, "sale", "customer_unresolved", f"customer {txn.customer_id} does not exist")

    live_item = _live_stock_item_for_vin(db, txn.tenant_id, vehicle_mdm.vin)
    if live_item is not None:
        return _vehicle_known_to_live_stock(txn, "sale", live_item, vehicle_mdm)
    vin_items = _stock_items_for_vin(db, txn.tenant_id, vehicle_mdm.vin)
    if vin_items.in_stock is None and vin_items.latest_sold is not None:
        sold = vin_items.latest_sold
        return RowOutcome(
            txn.id, "sale", "vehicle_already_in_stock",
            _VEHICLE_ALREADY_IN_STOCK_NOTE.format(
                item_id=sold.id, mdm_id=vehicle_mdm.id, vin=vehicle_mdm.vin,
                conflict=f"a resale (left_stock_at={sold.left_stock_at})", row_type="sale",
            ),
        )

    condition = _CONDITION_MAP[legacy_vehicle.condition]
    lossy_note = ""
    if legacy_vehicle.condition == LegacyVehicleCondition.CERTIFIED_PRE_OWNED:
        lossy_note = (
            f"legacy condition 'certified_pre_owned' has no StockItemCondition equivalent — "
            f"approximated as 'used' for transaction {txn.id}"
        )

    if not commit:
        note = f"would resolve/create a stock item + confirmed contract for vehicle {vehicle_mdm.id}"
        return RowOutcome(txn.id, "sale", "migrated", (lossy_note + "; " if lossy_note else "") + note)

    # run_migration already rejected amount_missing/transaction_date_missing
    # before ever dispatching here — narrows the type for mypy and doubles
    # as a defensive invariant check against a future direct caller.
    assert txn.amount is not None
    assert txn.transaction_date is not None

    # A sale is definitionally the vehicle leaving stock at this price,
    # however it got into stock — the stay an earlier-processed trade-in
    # opened (its in_stock_at/acquisition fields are that trade-in's own,
    # more accurate than anything this row could supply, and are left
    # untouched), or an item created here when none is in stock.
    created = vin_items.in_stock is None
    stock_item = vin_items.in_stock or _create_stock_item(
        db, tenant_id=txn.tenant_id, vehicle_mdm=vehicle_mdm, legacy_vehicle=legacy_vehicle, condition=condition,
    )
    if created:
        stock_item.in_stock_at = txn.transaction_date
    stock_item.left_stock_at = txn.transaction_date  # ADR-054: "sold" is not a lifecycle value
    stock_item.base_price = txn.amount
    stock_item.list_price = txn.amount
    stock_item.effective_price = txn.amount
    stock_item.updated_by = None
    db.flush()

    offer = SalesOffer(
        tenant_id=txn.tenant_id,
        offer_number=allocate_offer_number(db, txn.tenant_id),
        status=OfferStatus.OPEN,
        customer_id=customer.id,
        customer_label=_customer_label(customer),
        vehicle_source="stock",
        stock_item_id=stock_item.id,
        vehicle_label=stock_item.vehicle_label,
        created_by=None,
        updated_by=None,
    )
    db.add(offer)
    db.flush()

    # The real production code path — not a hand-rolled duplicate — so the
    # migrated offer's snapshot and build-up are indistinguishable in
    # shape from one generated live.
    freeze_vehicle_snapshot(db, offer=offer)
    apply_build_up(db, offer=offer)
    db.flush()

    contract = SalesContract(
        tenant_id=txn.tenant_id,
        contract_number=allocate_contract_number(db, txn.tenant_id),
        offer_id=offer.id,
        offer_number=offer.offer_number,
        status=ContractStatus.CONFIRMED,
        customer_id=offer.customer_id,
        customer_label=offer.customer_label,
        vehicle_source=offer.vehicle_source,
        stock_item_id=offer.stock_item_id,
        vehicle_label=offer.vehicle_label,
        base_price=offer.base_price,
        options_total=offer.options_total,
        list_price=offer.list_price,
        accessories_total=offer.accessories_total,
        discount_amount=offer.discount_amount,
        gross_price=offer.gross_price,
        margin=offer.margin,
        payable=offer.gross_price,
        financing=FinancingKind.CASH,
        signed_at=txn.transaction_date,
        legacy_transaction_id=txn.id,
        created_by=None,
        updated_by=None,
    )
    db.add(contract)
    db.flush()

    note = f"stock_item={stock_item.id}{' (reused)' if not created else ''} offer={offer.id} contract={contract.id}"
    return RowOutcome(
        txn.id, "sale", "migrated", (lossy_note + "; " if lossy_note else "") + note, new_contract_id=contract.id,
        new_stock_item_id=stock_item.id,
    )


def _migrate_trade_in(db: Session, txn: Transaction, *, commit: bool) -> RowOutcome:
    legacy_vehicle = db.get(LegacyVehicle, txn.vehicle_id)
    if legacy_vehicle is None:
        return RowOutcome(txn.id, "trade_in", "vehicle_unresolved", f"legacy vehicle {txn.vehicle_id} does not exist")
    vehicle_mdm = _resolve_vehicle_mdm(db, legacy_vehicle.id)
    if vehicle_mdm is None:
        return RowOutcome(
            txn.id, "trade_in", "vehicle_unresolved",
            f"legacy vehicle {legacy_vehicle.id} has no vehicle_mdm row carrying it as "
            "migrated_from_legacy_vehicle_id — run scripts/migrate_legacy_vehicles.py first",
        )
    customer = db.get(Customer, txn.customer_id)
    if customer is None:
        return RowOutcome(txn.id, "trade_in", "customer_unresolved", f"customer {txn.customer_id} does not exist")
    dealership = db.get(Dealership, txn.tenant_id)
    if dealership is None:
        return RowOutcome(txn.id, "trade_in", "dealership_unresolved", f"dealership {txn.tenant_id} does not exist")

    # A sold item for this VIN is history and plays no part: a car traded
    # back in after a sale is a new stay in stock, so a new item (KAN-256).
    live_item = _live_stock_item_for_vin(db, txn.tenant_id, vehicle_mdm.vin)
    if live_item is not None:
        return _vehicle_known_to_live_stock(txn, "trade_in", live_item, vehicle_mdm)
    in_stock = _stock_items_for_vin(db, txn.tenant_id, vehicle_mdm.vin).in_stock
    if in_stock is not None:
        return RowOutcome(
            txn.id, "trade_in", "vehicle_already_in_stock",
            _VEHICLE_ALREADY_IN_STOCK_NOTE.format(
                item_id=in_stock.id, mdm_id=vehicle_mdm.id, vin=vehicle_mdm.vin,
                conflict=f"acquisition data (purchase_price={in_stock.purchase_price})", row_type="trade_in",
            ),
        )

    condition = _CONDITION_MAP[legacy_vehicle.condition]
    lossy_note = ""
    if legacy_vehicle.condition == LegacyVehicleCondition.CERTIFIED_PRE_OWNED:
        lossy_note = (
            f"legacy condition 'certified_pre_owned' has no StockItemCondition equivalent — "
            f"approximated as 'used' for transaction {txn.id}"
        )

    if not commit:
        note = f"would create a Stock acquisition (in_stock) for vehicle {vehicle_mdm.id}"
        return RowOutcome(txn.id, "trade_in", "migrated", (lossy_note + "; " if lossy_note else "") + note)

    # run_migration already rejected amount_missing/transaction_date_missing
    # before ever dispatching here — narrows the type for mypy and doubles
    # as a defensive invariant check against a future direct caller.
    assert txn.amount is not None
    assert txn.transaction_date is not None

    supplier_is_vat_registered = customer.vat_registered
    notional_applicable = not supplier_is_vat_registered
    notional_rate = dealership.vat_rate if notional_applicable else None
    notional_amount = (
        _compute_notional_input_tax(purchase_price=txn.amount, rate=dealership.vat_rate)
        if notional_applicable
        else None
    )

    # A new stay in stock, whether the VIN was never seen before or was
    # sold earlier and is now reacquired as a trade-in — an ordinary
    # used-car-dealer pattern, not a corner case.
    stock_item = _create_stock_item(
        db, tenant_id=txn.tenant_id, vehicle_mdm=vehicle_mdm, legacy_vehicle=legacy_vehicle, condition=condition,
    )
    stock_item.in_stock_at = txn.transaction_date
    # Same idempotency mechanism the live consumer path uses
    # (app.inventory.services.pipeline._create_pipeline_item_idempotent)
    # — a unique index on (tenant_id, pipeline_ref) already exists for
    # exactly this purpose, so this migration reuses it rather than
    # adding a legacy_transaction_id column to StockItem the way
    # SalesContract carries one for the sale path. It also marks the item
    # as this import's (`_live_stock_item_for_vin`).
    stock_item.pipeline_ref = f"{_LEGACY_PIPELINE_REF_PREFIX}{txn.id}"
    stock_item.supplier_name = _customer_label(customer)
    stock_item.supplier_is_vat_registered = supplier_is_vat_registered
    stock_item.purchase_date = txn.transaction_date.date()
    stock_item.purchase_price = txn.amount
    stock_item.landed_cost = None  # never recorded on the legacy row — honestly unset, not invented
    stock_item.notional_input_tax_applicable = notional_applicable
    stock_item.notional_input_tax_rate = notional_rate
    stock_item.notional_input_tax_amount = notional_amount
    # Mirrors what record_purchase would have flipped once IN_STOCK +
    # purchase_price are both present — set directly rather than via
    # mark_purchased_if_ready, which also emits
    # inventory.stock_item.purchased (forbidden here; see NO OUTBOX
    # EVENTS in the module docstring).
    stock_item.is_invoiceable = True
    stock_item.updated_by = None
    db.flush()
    # KAN-100 — Sales learns a purchase from inventory.stock_item.purchased,
    # which this script must not publish; it records the same fact in Sales'
    # replica directly (event_id=None), or a later contract on this car could
    # never be invoiced.
    record_stock_item_purchased(
        db,
        tenant_id=stock_item.tenant_id,
        stock_item_id=stock_item.id,
        event_id=None,
        stock_item_label=stock_item.stock_number,
    )

    note = f"stock_item={stock_item.id}"
    return RowOutcome(
        txn.id, "trade_in", "migrated", (lossy_note + "; " if lossy_note else "") + note,
        new_stock_item_id=stock_item.id,
    )


def run_migration(db: Session, *, commit: bool) -> MigrationReport:
    # Ordered by transaction_date, with id as a tiebreak for determinism
    # across two rows sharing a date — so a trade-in is always seen
    # before its own later resale (you cannot resell a vehicle before
    # acquiring it). nulls_last() keeps a NULL-dated row (rejected below
    # as transaction_date_missing regardless of position) from silently
    # reordering everything else differently between Postgres (NULLS
    # LAST by default) and SQLite (NULLS FIRST by default).
    rows = list(
        db.scalars(
            select(Transaction).order_by(Transaction.transaction_date.asc().nulls_last(), Transaction.id)
        ).all()
    )
    outcomes: list[RowOutcome] = []

    for txn in rows:
        try:
            existing_contract = db.scalar(select(SalesContract).where(SalesContract.legacy_transaction_id == txn.id))
            existing_stock_item = db.scalar(
                select(StockItem).where(StockItem.pipeline_ref == f"{_LEGACY_PIPELINE_REF_PREFIX}{txn.id}")
            )
            if existing_contract is not None or existing_stock_item is not None:
                outcomes.append(RowOutcome(txn.id, txn.transaction_type.value, "already_migrated"))
                continue
            if txn.status != TransactionStatus.COMPLETED:
                outcomes.append(
                    RowOutcome(txn.id, txn.transaction_type.value, "not_completed", f"status={txn.status.value}")
                )
                continue
            if txn.amount is None:
                outcomes.append(
                    RowOutcome(
                        txn.id, txn.transaction_type.value, "amount_missing",
                        "a completed transaction with no amount — needed as the sale price or trade-in purchase "
                        "price; reported rather than defaulted to zero",
                    )
                )
                continue
            if txn.transaction_date is None:
                outcomes.append(
                    RowOutcome(
                        txn.id, txn.transaction_type.value, "transaction_date_missing",
                        "a completed transaction with no transaction_date — needed for in_stock_at/purchase_date/"
                        "left_stock_at and for this migration's own processing order; reported rather than guessed",
                    )
                )
                continue
            if txn.transaction_type == TransactionType.TRADE_IN:
                outcomes.append(_migrate_trade_in(db, txn, commit=commit))
                continue
            outcomes.append(_migrate_sale(db, txn, commit=commit))
        except Exception as exc:  # noqa: BLE001 — a migration tool reports, it does not crash
            db.rollback()
            outcomes.append(
                RowOutcome(txn.id, txn.transaction_type.value, "error", f"{type(exc).__name__}: {exc}")
            )
            return MigrationReport(committed=False, total_rows=len(rows), outcomes=outcomes, aborted=True)

    if commit:
        db.commit()
    else:
        db.rollback()

    return MigrationReport(committed=commit, total_rows=len(rows), outcomes=outcomes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write. Omit for a dry run (default) — read the report first.",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        report = run_migration(db, commit=args.commit)
    finally:
        db.close()

    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())

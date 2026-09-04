"""One-way migration off the shipped `transaction` table (WP-8 PR-7,
ADR-050, KAN-26). Idempotent and re-runnable, keyed on
`SalesContract.legacy_transaction_id` (see the model comment there): a
row already migrated is reported and skipped, never migrated twice.

MANDATORY DRY RUN. Default mode is dry-run — it produces the row-level
report below and writes NOTHING. Nothing commits until a human has read
that report and re-runs with --commit, same convention as
migrate_legacy_vehicles.py.

SCOPE — only `status = completed` rows migrate. A `draft` or `cancelled`
transaction never became a real sale; migrating one into a confirmed
contract would fabricate a deal that never closed. Reported as
`not_completed`, never touched.

NO OUTBOX EVENTS. This is a one-time historical backfill, not a new
business event — publishing sales.contract.confirmed /
inventory.stock_item.purchased etc. for a sale that happened years ago
would make live consumers (a real stock reservation, a real pipeline
auto-create) treat old history as something happening today. Rows are
written directly via the ORM, same as migrate_legacy_vehicles.py's own
precedent (that script publishes nothing either).

`sale` -> a confirmed sales_contract with a SYNTHESISED offer (ADR-050's
own word) AND a retroactively-created StockItem for the sold vehicle —
NOT a plain-text vehicle_label. The product owner was explicit that a
migrated contract needs the same concrete, joinable stock_item_id link
every other contract gets, and the same real frozen vehicle_snapshot
(via freeze_vehicle_snapshot / apply_build_up — the actual production
code path, not a hand-rolled duplicate), not a string description. The
StockItem is retroactive: lifecycle_status=IN_STOCK with in_stock_at AND
left_stock_at both set to the transaction date (ADR-054 — "sold" is not
a lifecycle value, `left_stock_at IS NOT NULL` is what "left stock"
means), base/list/effective price all set to the transaction's amount
(the only money figure the old table ever recorded — there is no
options/discount breakdown to reconstruct, so those stay at their
derived zero rather than being invented) so build_up() reproduces the
real historical gross price exactly. purchase_price/landed_cost are
left unset: the old table never recorded acquisition cost for a `sale`
row, so cost_basis/margin on the migrated contract are honestly None,
never guessed at.

`trade_in` -> Stock acquisition. NOT BUILT — see the REJECTED_TRADE_IN
docstring below for why every trade_in row rejects unconditionally, by
design, not as a bug in this script.

Per-row outcome, in the report:
- `not_completed`       — status was draft or cancelled. Never touched.
- `vehicle_unresolved`  — Transaction.vehicle_id is a legacy vehicle.id
  with no vehicle_mdm row carrying it as migrated_from_legacy_vehicle_id
  (scripts/migrate_legacy_vehicles.py never ran for it, or it was
  rejected there). REPORTED, never guessed at.
- `customer_unresolved` — Transaction.customer_id does not resolve to an
  existing Customer row.
- `condition_lossy`     — the legacy Vehicle's own condition was
  certified_pre_owned, which StockItemCondition has no value for;
  approximated as USED (matches migrate_legacy_vehicles.py's own
  totaled->scrapped precedent: flagged, not silent).
- `migrated`            — a sales_contract + synthesised sales_offer +
  retroactive StockItem were created (or would be, in dry-run).
- `rejected_trade_in`   — every trade_in row, unconditionally. See below.
- `already_migrated`    — this transaction id is already stamped on a
  sales_contract. A re-run reports every row here and changes nothing.

Usage:
    DMS_DATABASE_URL=... python scripts/migrate_transaction_rows.py            # dry run (default)
    DMS_DATABASE_URL=... python scripts/migrate_transaction_rows.py --commit   # writes, after you've read the report
"""

import argparse
import dataclasses
import sys
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models.customer import Customer, CustomerType
from app.db import SessionLocal
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.services.stock_item import allocate_stock_number
from app.sales.models.contract import ContractStatus, FinancingKind, SalesContract
from app.sales.models.offer import OfferStatus, SalesOffer
from app.sales.models.transaction import Transaction, TransactionStatus, TransactionType
from app.sales.services.numbering import allocate_contract_number, allocate_offer_number
from app.sales.services.pricing import apply_build_up
from app.sales.services.snapshot import freeze_vehicle_snapshot
from app.vehicle.models.vehicle import Vehicle as LegacyVehicle
from app.vehicle.models.vehicle import VehicleCondition as LegacyVehicleCondition
from app.vehicle.models.vehicle_mdm import VehicleMdm

_REJECTED_TRADE_IN_REASON = (
    "trade_in migration needs supplier_is_vat_registered (Stock's RecordPurchaseRequest "
    "requires it, non-optional) and a vehicle condition — neither exists anywhere on the "
    "legacy transaction row. Guessing supplier_is_vat_registered would silently misstate "
    "the fiktiver Vorsteuerabzug on a real VAT-relevant figure. Filed as a Notion bug "
    "(KAN-26 follow-up) rather than defaulted here, per the product owner's own instruction: "
    "'if it's missing then it's a bug which should be opened separately,' not a value to guess."
)

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
            if row.outcome in ("vehicle_unresolved", "customer_unresolved", "condition_lossy", "error"):
                lines.append(f"    {row.outcome.upper()} {row.transaction_type} {row.transaction_id}: {row.notes}")
        return "\n".join(lines)


def _resolve_vehicle_mdm(db: Session, legacy_vehicle_id: uuid.UUID) -> VehicleMdm | None:
    return db.scalar(select(VehicleMdm).where(VehicleMdm.migrated_from_legacy_vehicle_id == legacy_vehicle_id))


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

    condition = _CONDITION_MAP[legacy_vehicle.condition]
    lossy_note = ""
    if legacy_vehicle.condition == LegacyVehicleCondition.CERTIFIED_PRE_OWNED:
        lossy_note = (
            f"legacy condition 'certified_pre_owned' has no StockItemCondition equivalent — "
            f"approximated as 'used' for transaction {txn.id}"
        )

    if not commit:
        note = f"would create stock item + confirmed contract for vehicle {vehicle_mdm.id}"
        return RowOutcome(txn.id, "sale", "migrated", (lossy_note + "; " if lossy_note else "") + note)

    amount = txn.amount or Decimal(0)
    stock_item = StockItem(
        tenant_id=txn.tenant_id,
        stock_number=allocate_stock_number(db, txn.tenant_id),
        vehicle_id=vehicle_mdm.id,
        vin=vehicle_mdm.vin,
        vehicle_label=_vehicle_label(legacy_vehicle),
        condition=condition,
        base_price=amount,
        list_price=amount,
        effective_price=amount,
        lifecycle_status=LifecycleStatus.IN_STOCK,
        in_stock_at=txn.transaction_date,
        left_stock_at=txn.transaction_date,  # ADR-054: "sold" is not a lifecycle value
        created_by=None,
        updated_by=None,
    )
    db.add(stock_item)
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
        is_invoiceable=True,
        legacy_transaction_id=txn.id,
        created_by=None,
        updated_by=None,
    )
    db.add(contract)
    db.flush()

    note = f"stock_item={stock_item.id} offer={offer.id} contract={contract.id}"
    return RowOutcome(
        txn.id, "sale", "migrated", (lossy_note + "; " if lossy_note else "") + note, new_contract_id=contract.id
    )


def _migrate_trade_in(txn: Transaction) -> RowOutcome:
    return RowOutcome(txn.id, "trade_in", "rejected_trade_in", _REJECTED_TRADE_IN_REASON)


def run_migration(db: Session, *, commit: bool) -> MigrationReport:
    rows = list(db.scalars(select(Transaction)).all())
    outcomes: list[RowOutcome] = []

    for txn in rows:
        try:
            existing = db.scalar(select(SalesContract).where(SalesContract.legacy_transaction_id == txn.id))
            if existing is not None:
                outcomes.append(RowOutcome(txn.id, txn.transaction_type.value, "already_migrated"))
                continue
            if txn.status != TransactionStatus.COMPLETED:
                outcomes.append(
                    RowOutcome(txn.id, txn.transaction_type.value, "not_completed", f"status={txn.status.value}")
                )
                continue
            if txn.transaction_type == TransactionType.TRADE_IN:
                outcomes.append(_migrate_trade_in(txn))
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

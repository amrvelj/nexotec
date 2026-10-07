"""SalesContract (WP-8 PR-1, S-D01/S-D06): contract = order for v1, no
separate Order entity. `offer_id` is nullable — the confirmed reference
prototype shows both a "Vertrag erzeugen" path (from an existing offer,
which denormalizes the offer's own number as lineage: "C-001195 ← O-003216")
and a direct "Vertrag erstellen" primary action on a stock item's detail
header with no prior offer at all.

`offer_id` carries no DB FK by house convention (a cross-context id would
never have one; here it is same-context but still deliberately opaque,
since a contract's whole point is that it can outlive being "the offer
that became this contract" — same posture PRD-Sales v2 gives it).

`status` has FOUR values, not three — `invoiced` is a real value the
reference prototype's own grid shows ("Fakturiert"), but WP-8 emits no
code path that sets it: finance (WP-9+) is what will flip a contract to
`invoiced` on `finance.invoice.issued`, once that context exists. Declaring
the value now keeps the schema honest about what the confirmed UI actually
renders, without inventing a fake trigger for it.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import DECIMAL, Date, String, exists
from sqlalchemy.orm import Mapped, column_property, declared_attr, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base
from app.sales.models.stock_item_purchase import SalesStockItemPurchase


class FinancingKind(str, enum.Enum):
    CASH = "cash"
    LEASING = "leasing"
    CREDIT = "credit"


class ContractStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    INVOICED = "invoiced"


class SalesContract(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "sales_contract"

    contract_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # Opaque lineage, set once at creation, never repointed. Null for a
    # contract created directly (S-D01's "two linked entities" does not
    # require the link to exist).
    offer_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    offer_number: Mapped[str | None] = mapped_column(String(16), nullable=True)

    status: Mapped[ContractStatus] = mapped_column(
        StoredEnum(ContractStatus, length=16), nullable=False, default=ContractStatus.PENDING
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the customer context. No DB-level FK."
    )
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    customer_language: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # WP-8 PR-6 — copied from the offer at creation (S-D09/ADR-045's
    # own vocabulary): "existing" once confirmed means the reservation targets a
    # real stock_item_id; "manual" means handle_sales_contract_confirmed
    # (already built, WP-7) materializes a new pipeline stock item instead.
    vehicle_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "stock" | "manual"
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    # Rule 2's display label (the stock number) and when it was copied. Set
    # when Sales learns a manual configuration's pipeline item (KAN-144);
    # null on a stock-sourced contract, where Sales never sees the number.
    stock_item_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stock_item_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    vehicle_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manual_vehicle_condition: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # WP-8 PR-7 — the full build-up, copied from the offer at creation so
    # the contract's own generated document (services/document.py) can
    # itemize base -> options -> list -> discount -> price, exactly like
    # the confirmed live Preisaufbau tab on an already-confirmed contract.
    base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    options_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    accessories_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # WP-8 PR-3 — copied from the offer at the moment of creation (like
    # gross_price above); entity-private, same posture as
    # SalesOffer.margin. A direct contract (no offer) has no pricing
    # build-up of its own yet, so this stays None.
    margin: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # WP-8 PR-6 — trade-in, copied from the offer at creation (S-D18's
    # "vehicle AND allocation happen at OFFER time" — the contract only
    # carries the read-only reference forward). At confirmation (KAN-101)
    # the valuation is consumed through app.valuation.public, and its id
    # travels in the sales.contract.confirmed payload so inventory can set
    # the pipeline stock item's valuation pointer when it creates the item.
    trade_in_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    trade_in_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trade_in_vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    trade_in_valuation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the valuation context (Valuation.id). No DB-level FK."
    )
    trade_in_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    trade_in_purchase_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    payable: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    financing: Mapped[FinancingKind | None] = mapped_column(
        StoredEnum(FinancingKind, length=16), nullable=True
    )

    # WP-8 PR-6 (ADR-047, Pattern B) — set by confirm_contract's own
    # reserve_for_contract() call; opaque, matches app.inventory's own
    # StockItem.active_reservation_id shape (no DB FK, cross-context id).
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    signed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    delivery_date: Mapped[dt.date | None] = mapped_column(Date(), nullable=True)

    @declared_attr
    def is_invoiceable(cls) -> Mapped[bool]:
        """ADR-052 / KAN-100 — derived on read from Sales' local replica of
        Stock's purchase fact (SalesStockItemPurchase), in the same SELECT
        that loads the contract; never stored, never read live from Stock.
        A stored copy could miss a purchase consumed while the contract was
        being written, and nothing would ever correct it. False for a
        contract with no stock item (a manual configuration: Sales is not
        told the pipeline item it becomes)."""

        return column_property(
            exists().where(
                SalesStockItemPurchase.tenant_id == cls.tenant_id,
                SalesStockItemPurchase.stock_item_id == cls.stock_item_id,
            )
        )
    # Populated later by finance (WP-9+, out of scope) on
    # finance.invoice.issued — declared now for the same reason
    # ContractStatus.INVOICED is declared now.
    invoice_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    cancelled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # KAN-26 (ADR-050): provenance + idempotency key for
    # scripts/migrate_transaction_rows.py, matching VehicleMdm.
    # migrated_from_legacy_vehicle_id's own precedent — never used for
    # lookups by the live application, purely an audit trail and a
    # "already migrated, do not re-migrate" guard. Null for every
    # ordinarily-created contract.
    legacy_transaction_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)

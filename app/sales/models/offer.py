"""SalesOffer (WP-8 PR-1, S-D01): "a quote for one customer and one car,
not yet a deal." An offer starts as a bare draft — the reference prototype
allocates a number ("O-003347") before Kunde or Fahrzeug are chosen — and
is filled in by the container-based generation workspace (PR-2 onward).

`offer_number` is an immutable business key, never reused (same posture as
app.customer's customer_number — see app/customer/services/customer.py's
own reasoning: a reused number would silently point at two different
records over time).

`customer_id`/`customer_label`/`customer_locality` follow the "split by
staleness class" rule established for this package: offer_number itself is
an immutable business key and never refreshes; these three are genuinely
mutable display fields and share ONE refresh timestamp. What keeps them
current today: the buyer's attach (`offer.apply_customer_change`), the
`sales.customer_merged` consumer, which writes the survivor's label onto
every moved row (`services/customer_merge.py`, KAN-580), and the nightly
`sales.customer_labels` job (`services/customer_label.py`, KAN-590), which
re-reads the label and the correspondence language of a renamed customer on
open work — a draft or open offer no signed contract was made from, a pending
contract and their deal rows — within a day; a signed contract and its offer
keep the name the buyer signed under (Anto, 2026-11-10). `customer_locality` is written by no path at all and stays so
(Anto, 2026-11-10: neither filled nor dropped).

`vehicle_label` here is a plain mutable working field, distinct from the
ADR-041 *frozen* `vehicle_snapshot` that PR-3 adds once pricing exists —
before a snapshot is taken there is nothing to freeze yet.
"""

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import PrimaryKeyMixin, TenantScopedMixin, TimestampMixin, VersionedMixin
from app.core.enum_type import StoredEnum
from app.core.types import GUID, UTCDateTime
from app.db import Base


class OfferStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    CANCELLED = "cancelled"


class SalesOffer(PrimaryKeyMixin, TenantScopedMixin, VersionedMixin, TimestampMixin, Base):
    __tablename__ = "sales_offer"

    offer_number: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[OfferStatus] = mapped_column(
        StoredEnum(OfferStatus, length=16), nullable=False, default=OfferStatus.DRAFT
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the customer context. No DB-level FK."
    )
    customer_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_locality: Mapped[str | None] = mapped_column(String(100), nullable=True)
    customer_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # WP-8 PR-7 — "the customer's correspondence language is not the
    # user's UI language" (CLAUDE.md's own rule). Denormalized alongside
    # customer_label at the same moment, same refresh timestamp — a
    # generated document (services/document.py) reads THIS, never the
    # generating seller's own UI locale.
    customer_language: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # KAN-200 (FR-A-37, D-A-35; Anto, 2026-10-19): the responsible seller —
    # the person whose open offer this is, as distinct from `created_by`
    # (who typed it, kept for the record, A-RULE-5). Defaults to the creator,
    # changes when a manager hands the offer to a colleague, and is cleared
    # ("unassigned", the sales list's own queue) when a leaver's open offers
    # go to nobody. Rule 2's three-column pattern: a platform User id, its
    # display label and when the label was read — never a FK.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, index=True, comment="Owned by the platform context (User). No DB-level FK."
    )
    owner_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # WP-8 PR-2 (FR-S-08's two-path vehicle container): "stock" means
    # stock_item_id is the source of truth; "manual" means vehicle_label +
    # manual_vehicle_condition are hand-typed (ADR-045 — this never creates
    # a vehicle-mdm record; a pipeline stock item is only materialized on
    # contract confirmation, PR-6).
    vehicle_source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "stock" | "manual"
    stock_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the inventory context (StockItem.id). No DB-level FK."
    )
    # Rule 2's display label (the stock number) and when it was copied —
    # once, when the car is put on the offer (KAN-157, Anto 2026-11-03,
    # option A: a stock number never changes). Cleared with stock_item_id.
    stock_item_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stock_item_denorm_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    vehicle_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manual_vehicle_condition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # C-F (KAN-10, FR-C-12) — offer Path B: the configuration this vehicle
    # was built in, a `build`-mode configuration owned by the vehicle
    # context (three-column pattern, rule 2). Set only with
    # vehicle_source == "manual". vehicle_label above is its display label
    # at attach time; the frozen vehicle_snapshot carries its spec block.
    configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,  # KAN-563: vehicle's purge asks `referenced_configuration_ids` by this column
        comment="Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK.",
    )
    configuration_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    configuration_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # WP-8 PR-3 — a manual configuration has no stock item to price from,
    # so the seller enters this directly; unlike a stock vehicle's price
    # (frozen from live data, ADR-041), this is a plain mutable field with
    # no external source to protect against, same posture as vehicle_label.
    manual_base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # WP-8 PR-2 (S-D03) — the Leasing container is a refused calculator:
    # free-text inputs only, captured so a later real calculator can be
    # dropped in without a schema change, never computed here.
    leasing_down_payment: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    leasing_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leasing_km_per_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # WP-8 PR-3 (ADR-041) — frozen once, from app.inventory.public.
    # get_stock_item_pricing's plain dict, never re-read from live stock
    # data afterward: "a later catalogue correction never changes an
    # existing offer" (confirmed live, verbatim, on the reference
    # prototype's own Preisaufbau footer). Re-frozen only if the vehicle
    # identity itself changes (a different stock item, or manual details
    # edited) — see services/snapshot.py::freeze_vehicle_snapshot.
    vehicle_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    vehicle_snapshot_frozen_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # KAN-553 (ADR-115): Stock changed the car's commercial data after the
    # freeze (`inventory.stock_item.commercial_changed`) — when it last did,
    # and which fields (merged). The snapshot stays frozen (ADR-041); refresh
    # or dismiss clears both.
    vehicle_changed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    vehicle_changed_fields: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # «Ignorieren»: the seller kept the frozen car as of then; the nightly
    # check compares Stock's later changes only.
    vehicle_change_dismissed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # The car's {listPrice, effectivePrice} the seller accepted with «Ignorieren».
    vehicle_change_dismissed_prices: Mapped[dict[str, str | None] | None] = mapped_column(JSON, nullable=True)

    # WP-8 PR-3 — pricing.build_up()'s materialized result. base -> options
    # -> list -> accessories -> total -> discount -> price (FR-S spec's own
    # level order). discount_type/discount_value are the seller's input;
    # everything else here is derived, never independently editable.
    base_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    options_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    accessories_total: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    total_before_discount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "percent" | "amount"
    discount_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Materialized total — pricing.build_up()'s final `grossPrice`. What
    # the grid and this row itself display; the printed document's own
    # ONE price (ADR-057/S-D10) is this same figure, VAT included. The
    # document's one VAT line is the component contained in it, computed
    # at render time (document.py::_compute_vat_amount, ADR-098) and never
    # stored here; nothing is ever added on top.
    gross_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # WP-8 PR-3 (ADR-049/ADR-029) — Einstandspreis, sourced from the frozen
    # snapshot's own purchasePrice; None for a manual configuration, which
    # has no real stock-item cost to compare against. Entity-private: no
    # group-read endpoint exists anywhere for sales_offer/sales_contract or
    # sales_deal (see app/sales/models/deal.py's own docstring) — that
    # absence, not a filtered column, is what ADR-029 relies on here.
    cost_basis: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    margin: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    # The vehicle-mdm record of the traded-in car, when the register already
    # holds it: since KAN-189 (Anto, 2026-10-31 B / 2026-11-01 A) the trade-in
    # only LINKS an existing record (vehicle.public.match_vehicle) and creates
    # none, and allocates no owner/keeper; a VIN-only trade-in keeps
    # trade_in_vin, and the car enters the register at its purchase
    # (ADR-103), when its customer is recorded as former owner and keeper
    # (customer.trade_in_purchased). Sales never owns the vehicle.
    trade_in_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the vehicle context (VehicleMdm.id). No DB-level FK."
    )
    trade_in_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trade_in_vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    # C-F (KAN-10) — a trade-in captured through the valuation path in
    # `record` mode (FR-C-12's carve-out, S-D11) carries the valuation's
    # configuration; trade_in_label is its display label. No vehicle-mdm
    # record exists for such a trade-in (ADR-070), so trade_in_vehicle_id
    # stays null.
    trade_in_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        nullable=True,
        index=True,  # KAN-563: vehicle's purge asks `referenced_configuration_ids` by this column
        comment="Owned by the vehicle context (VehicleConfiguration.id). No DB-level FK.",
    )
    trade_in_configuration_label_refreshed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # S-D04: the valuation is a read-only REFERENCE — Sales never copies
    # its inputs/deductibles, only its id and final_offer (three-column
    # pointer, same shape as app.inventory.StockItem.valuation_ref_*).
    trade_in_valuation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), nullable=True, comment="Owned by the valuation context (Valuation.id). No DB-level FK."
    )
    # The FINAL OFFER (ADR-060's own tradeInValue) — what the customer
    # sees on the document, never the valuation's raw net-after-deductions
    # figure and never the entity-private purchase price below.
    trade_in_value: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # S-D04: "deductibles + final-offer are separate" from what the
    # dealership will actually book on acquisition — entity-private
    # (never on the customer document), seller-adjustable independently of
    # trade_in_value (the negotiated trade-in offer and the eventual
    # Wagenbuch acquisition entry are not required to be the same number).
    trade_in_purchase_price: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)
    # Materialized = gross_price - trade_in_value (confirmed live "Zu
    # bezahlen" stat).
    payable: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 2), nullable=True)

    cancelled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # KAN-12 / PRD-Sales v2's own versioning section: "`Copy Offer` starts
    # a new lineage at version 1 and records `copiedFromOfferId`." A real
    # FK (unlike the cross-context GUID columns above) — this points at
    # another row in this SAME table/context, so rule 2's "no cross-context
    # FK" doesn't apply. Nullable: most offers are not copies.
    copied_from_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("sales_offer.id"), nullable=True, index=True
    )

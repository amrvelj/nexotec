"""SalesOffer schemas (WP-8 PR-1). Creation takes no body — the confirmed
reference prototype allocates a number and opens an empty draft before
anything is chosen; every field is filled in afterward through the
autosave PATCH the container workspace uses (PR-2).
"""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.sales.models.offer import OfferStatus


class OfferContainerState(CamelModel):
    """WP-8 PR-2 — one entry per workspace container, computed server-side
    (never client-derived) so the sticky footer's missing-requirements list
    and each container's own status badge always agree, by construction.
    """

    id: str  # "customer" | "vehicle" | "pricing" | "trade_in" | "leasing"
    requirement: str  # "required" | "optional"
    status: str  # "not_started" | "in_progress" | "complete" | "placeholder"


class TradeInRequest(CamelModel):
    """WP-8 PR-5 (S-D18/ADR-064) — the one-step trade-in action. Not a
    plain PATCH field set: it calls into vehicle-mdm and customer, so it
    is its own endpoint (POST /v1/sales/offers/{id}/trade-in), same
    reasoning as reservation being its own endpoint rather than a PATCH.
    """

    vin: str | None = None
    plate: str | None = None
    canton: str | None = None
    vehicle_label: str
    # None = same customer as the offer's own Kunde container.
    customer_id: uuid.UUID | None = None


class AttachValuationRequest(CamelModel):
    valuation_id: uuid.UUID


class OfferUpdate(CamelModel):
    """The container workspace's own autosave PATCH (FR-S-05) — every
    field optional, applied incrementally as the seller fills in whichever
    container they touch, in any order (S-D03/FR-S-05's "non-linear"
    contract) except pricing which needs a vehicle first.
    """

    customer_id: uuid.UUID | None = None
    vehicle_source: str | None = None  # "stock" | "manual"
    stock_item_id: uuid.UUID | None = None
    vehicle_label: str | None = None
    manual_vehicle_condition: str | None = None
    manual_base_price: Decimal | None = None
    leasing_down_payment: Decimal | None = None
    leasing_term_months: int | None = None
    leasing_km_per_year: int | None = None
    # WP-8 PR-3 — the seller's own input into pricing.build_up(); every
    # other pricing field is derived and not client-settable.
    discount_type: str | None = None  # "percent" | "amount"
    discount_value: Decimal | None = None


class OfferRead(CamelModel):
    id: uuid.UUID
    offer_number: str
    status: OfferStatus
    customer_id: uuid.UUID | None
    customer_label: str | None
    customer_locality: str | None
    vehicle_source: str | None
    stock_item_id: uuid.UUID | None
    vehicle_label: str | None
    manual_vehicle_condition: str | None
    manual_base_price: Decimal | None
    leasing_down_payment: Decimal | None
    leasing_term_months: int | None
    leasing_km_per_year: int | None
    # WP-8 PR-3 — pricing.build_up()'s materialized result (base -> options
    # -> list -> accessories -> total -> discount -> price).
    base_price: Decimal | None
    options_total: Decimal | None
    list_price: Decimal | None
    accessories_total: Decimal | None
    total_before_discount: Decimal | None
    discount_type: str | None
    discount_value: Decimal | None
    discount_amount: Decimal | None
    gross_price: Decimal | None
    # ADR-049/029 — entity-private; never exposed via any group-scoped
    # endpoint (none exists for sales_offer/sales_deal at all).
    cost_basis: Decimal | None
    margin: Decimal | None
    vehicle_snapshot_frozen_at: dt.datetime | None
    trade_in_vehicle_id: uuid.UUID | None
    trade_in_label: str | None
    trade_in_vin: str | None
    trade_in_valuation_id: uuid.UUID | None
    trade_in_value: Decimal | None
    trade_in_purchase_price: Decimal | None
    payable: Decimal | None
    cancelled_reason: str | None
    containers: list[OfferContainerState] = Field(default_factory=list)
    # WP-8 PR-8 — computed in _offer_read, never a stored column: a stock
    # vehicle's condition lives only inside the frozen vehicle_snapshot
    # JSON blob, and a manual configuration's lives on its own dedicated
    # column; the frontend (per-line discount suppression on used
    # vehicles) needs one place to read either.
    vehicle_condition: str | None = None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class OfferCancelRequest(CamelModel):
    reason: str


class OfferPage(CamelModel):
    items: list[OfferRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

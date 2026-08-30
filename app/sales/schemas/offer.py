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
    leasing_down_payment: Decimal | None = None
    leasing_term_months: int | None = None
    leasing_km_per_year: int | None = None


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
    leasing_down_payment: Decimal | None
    leasing_term_months: int | None
    leasing_km_per_year: int | None
    gross_price: Decimal | None
    cancelled_reason: str | None
    containers: list[OfferContainerState] = Field(default_factory=list)
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

"""SalesOffer schemas (WP-8 PR-1). Creation takes no body — the confirmed
reference prototype allocates a number and opens an empty draft before
anything is chosen; every field is filled in afterward through the
autosave PATCH the container workspace uses (PR-2).
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.sales.models.offer import OfferStatus


class OfferRead(CamelModel):
    id: uuid.UUID
    offer_number: str
    status: OfferStatus
    customer_id: uuid.UUID | None
    customer_label: str | None
    customer_locality: str | None
    stock_item_id: uuid.UUID | None
    vehicle_label: str | None
    gross_price: Decimal | None
    cancelled_reason: str | None
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

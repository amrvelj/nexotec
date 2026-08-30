"""SalesContract schemas (WP-8 PR-1)."""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.sales.models.contract import ContractStatus


class ContractCreate(CamelModel):
    # None = a direct "Vertrag erstellen" contract with no prior offer
    # (confirmed live as the stock detail header's own primary action).
    offer_id: uuid.UUID | None = None


class ContractRead(CamelModel):
    id: uuid.UUID
    contract_number: str
    offer_id: uuid.UUID | None
    offer_number: str | None
    status: ContractStatus
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


class ContractCancelRequest(CamelModel):
    reason: str


class ContractPage(CamelModel):
    items: list[ContractRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

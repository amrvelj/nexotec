"""SalesContract schemas (WP-8 PR-1)."""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.sales.models.contract import ContractStatus, FinancingKind


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
    vehicle_source: str | None
    stock_item_id: uuid.UUID | None
    vehicle_label: str | None
    manual_vehicle_condition: str | None
    gross_price: Decimal | None
    margin: Decimal | None
    trade_in_vehicle_id: uuid.UUID | None
    trade_in_label: str | None
    trade_in_vin: str | None
    trade_in_valuation_id: uuid.UUID | None
    trade_in_value: Decimal | None
    trade_in_purchase_price: Decimal | None
    payable: Decimal | None
    financing: FinancingKind | None
    reservation_id: uuid.UUID | None
    signed_at: dt.datetime | None
    delivery_date: dt.date | None
    is_invoiceable: bool
    invoice_ref: str | None
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

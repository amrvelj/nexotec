import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel
from app.inventory.models.stock_item_ledger import LedgerCategory, LedgerDirection


class RecordCostRequest(CamelModel):
    category: LedgerCategory
    amount: Decimal
    occurred_at: dt.datetime
    source_ref: str


class LedgerEntryRead(CamelModel):
    id: uuid.UUID
    stock_item_id: uuid.UUID
    category: LedgerCategory
    direction: LedgerDirection
    amount: Decimal
    occurred_at: dt.datetime
    source_ref: str
    is_auto: bool
    created_at: dt.datetime


class LedgerEntryPage(CamelModel):
    items: list[LedgerEntryRead]

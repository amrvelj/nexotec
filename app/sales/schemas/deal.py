"""SalesDeal schemas — the overview grid's own read shape (ADR-060). A
distinct schema from OfferRead/ContractRead, matching app.inventory's own
"a separate, hand-authored schema, never the entity reused" convention for
a read model.
"""

import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel


class DealRead(CamelModel):
    id: uuid.UUID
    entity_type: str
    number: str
    status: str
    offer_id: uuid.UUID | None
    offer_number: str | None
    contract_id: uuid.UUID | None
    contract_number: str | None
    customer_id: uuid.UUID | None
    customer_label: str | None
    customer_locality: str | None
    vehicle_label: str | None
    gross_price: Decimal | None
    margin: Decimal | None
    documents_count: int
    created_at: dt.datetime
    updated_at: dt.datetime


class DealPage(CamelModel):
    items: list[DealRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

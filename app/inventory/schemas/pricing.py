import uuid
from decimal import Decimal

from app.core.schemas import CamelModel


class OptionInput(CamelModel):
    code: str
    label: str
    price: Decimal
    equipment_code: str | None = None


class OptionRead(CamelModel):
    id: uuid.UUID
    code: str
    label: str
    price: Decimal
    equipment_code: str | None


class SetOptionsRequest(CamelModel):
    base_price: Decimal
    options: list[OptionInput]

import uuid

from app.core.schemas import CamelModel


class ReserveRequest(CamelModel):
    contract_id: uuid.UUID


class ReservationRead(CamelModel):
    reservation_id: uuid.UUID
    stock_item_id: uuid.UUID

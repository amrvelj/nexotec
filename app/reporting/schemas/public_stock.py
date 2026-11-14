import datetime as dt
import uuid
from decimal import Decimal

from app.core.schemas import CamelModel


class PublicStockListingRead(CamelModel):
    """One car as the Betrieb's website shows it — the allow-listed fields
    only (ADR-029)."""

    stock_item_id: uuid.UUID
    location_id: uuid.UUID | None
    published_at: dt.datetime | None
    stock_number: str | None = None
    vehicle_label: str | None = None
    condition: str | None = None
    first_registration_date: dt.date | None = None
    odometer_km: int | None = None
    exterior_colour: str | None = None
    body_style: str | None = None
    price_chf: Decimal | None = None
    reserved: bool = False
    image_urls: list[str] = []


class PublicStockRead(CamelModel):
    dealership_id: uuid.UUID
    total: int
    items: list[PublicStockListingRead]

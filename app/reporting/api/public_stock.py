"""The public stock projection, read by staff (KAN-225, FR-A-27).

The hosted website is its real reader and reads it through
`reporting.public`; this route lets the publishing screen show what the
website shows. Tenant from the token, another Betrieb's id a 404; any role
that reads the publishing profile reads it. No entitlement gate (it is not a
licensed module's router): a Betrieb without Sales lists nothing, so it reads
an empty list.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal
from app.core.permissions import require_read
from app.core.tenancy import require_tenant_match
from app.db import get_db
from app.reporting.schemas.public_stock import PublicStockListingRead, PublicStockRead
from app.reporting.services.public_stock import PublicStockItem, list_public_stock

router = APIRouter(tags=["reporting"])

# The projection's keys (camelCase) -> the read's fields.
_FIELDS = {
    "stockNumber": "stock_number",
    "vehicleLabel": "vehicle_label",
    "condition": "condition",
    "firstRegistrationDate": "first_registration_date",
    "odometerKm": "odometer_km",
    "exteriorColour": "exterior_colour",
    "bodyStyle": "body_style",
    "priceChf": "price_chf",
    "reserved": "reserved",
    "imageUrls": "image_urls",
}


def _read(item: PublicStockItem) -> PublicStockListingRead:
    values = {_FIELDS[key]: value for key, value in item.listing.items() if key in _FIELDS and value is not None}
    return PublicStockListingRead(
        stock_item_id=item.stock_item_id, location_id=item.location_id, published_at=item.published_at, **values
    )


@router.get("/dealerships/{dealership_id}/public-stock", response_model=PublicStockRead)
def get_public_stock(
    dealership_id: uuid.UUID,
    location_id: uuid.UUID | None = Query(default=None, alias="locationId"),
    principal: Principal = Depends(require_read("publishing_profile")),
    db: Session = Depends(get_db),
):
    require_tenant_match(dealership_id, principal)
    items = list_public_stock(db, dealership_id=dealership_id, location_id=location_id)
    return PublicStockRead(dealership_id=dealership_id, total=len(items), items=[_read(i) for i in items])

"""FR-V-14 shared identity lookup response — deliberately a SEPARATE,
narrower schema from the tenant-scoped detail read, not the same model
with fields hidden. This is what makes the licence boundary (ADR-013,
ADR-043) a property of the response shape rather than a convention: there
is no field on this class that could regress into the response by a
future edit, because the class itself doesn't have one to add to by
mistake.

See tests/architecture/test_shared_identity_lookup_response_shape.py —
"this needs a test, not a code review," quoted directly from the brief.
"""

import datetime as dt
import uuid

from app.core.schemas import CamelModel
from app.vehicle.schemas.catalogue import CatalogueVariantPrice
from app.vehicle.schemas.spec_block import VehicleSpecBlockRead


class SharedVehicleIdentity(CamelModel):
    """ADR-112 (3), KAN-130/KAN-548: a vehicle already known in the platform
    carries its catalogue variant, the variant's list price and the spec
    block for every dealer, with or without a provider contract — that is
    master data (ADR-075). What stays out, by shape: options, colours, tyres,
    images and any other licensed detail (ADR-013)."""

    vin: str
    stammnummer: str | None
    type_approval_number: str | None
    first_registration_date: dt.date | None
    current_plate: str | None
    fuel_type: str | None
    body_style: str | None
    drivetrain: str | None
    vehicle_status: str
    catalogue_variant_id: uuid.UUID | None
    list_price: CatalogueVariantPrice | None
    specification: VehicleSpecBlockRead | None

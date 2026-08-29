"""WP-5 PR-6, ADR-013/ADR-043/ADR-045: "This needs a test on the response
shape, not a code review," quoted directly from the brief. Two layers:

1. The schema class itself (SharedVehicleIdentity) has EXACTLY the allowed
   field set — nothing more, nothing that could regress in by a future
   edit adding a field to the wrong model.
2. The ACTUAL wire response from a real HTTP call carries exactly that set
   too — not just the Python class, in case a future change bypasses the
   schema (e.g. returning a dict directly).
"""

import uuid

from app.core.auth import create_access_token
from app.vehicle.schemas.lookup import SharedVehicleIdentity
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

_ALLOWED_FIELDS = {
    "vin",
    "stammnummer",
    "typeApprovalNumber",
    "firstRegistrationDate",
    "currentPlate",
    "fuelType",
    "bodyStyle",
    "drivetrain",
    "vehicleStatus",
}

# Fields that must NEVER appear — the licensed specification block ADR-013
# explicitly forbids on a cross-tenant read: provider option lists, list
# prices, images, and the catalogue link itself (which would let a caller
# walk into the full variant record another tenant's provider contract pays
# for).
_FORBIDDEN_FIELDS = {
    "catalogueVariantId",
    "catalogueMatchStatus",
    "options",
    "optionList",
    "listPrice",
    "price",
    "images",
    "image",
    "specification",
}


def test_schema_class_has_exactly_the_allowed_field_set():
    camel_fields = {
        SharedVehicleIdentity.model_fields[name].alias or name for name in SharedVehicleIdentity.model_fields
    }
    assert camel_fields == _ALLOWED_FIELDS


def _token() -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=uuid.uuid4(), roles=frozenset(), is_dealer_manager=False
    )


def test_actual_wire_response_carries_exactly_the_allowed_fields(client, db_session):
    create_vehicle_mdm(db_session, vin="ZAR94000007123456", catalogue_variant_id=None)

    response = client.get(
        "/v1/vehicle-mdm/lookup", params={"vin": "ZAR94000007123456"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body.keys()) == _ALLOWED_FIELDS
    assert not (set(body.keys()) & _FORBIDDEN_FIELDS)

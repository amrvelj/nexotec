"""WP-5 PR-6, ADR-013/ADR-043/ADR-045: "This needs a test on the response
shape, not a code review," quoted directly from the brief. **Amended by
ADR-112 (3), KAN-548 (closes KAN-130):** a vehicle already known in the
platform carries its catalogue variant, the variant's list price and the spec
block for every dealer — master data auto-i-dat lets every dealer read
(ADR-075) — while options, colours, tyres, images and every other licensed
detail stay out of the shape (ADR-013). Two layers:

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
    # ADR-112 (3), KAN-548: master data for everyone (ADR-075).
    "catalogueVariantId",
    "listPrice",
    "specification",
}

# Fields that must NEVER appear — what ADR-013 keeps tenant-scoped on a
# cross-tenant read: provider option lists, colours, tyres, images, prices
# beyond the list price, and the tenant's own match status. (Until ADR-112
# the variant link, the list price and the spec block were here too; ADR-075
# makes them master data, readable by every dealer.)
_FORBIDDEN_FIELDS = {
    "catalogueMatchStatus",
    "options",
    "optionList",
    "colours",
    "tyreSpecs",
    "tyres",
    "price",
    "prices",
    "images",
    "image",
    "optionRelations",
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
    # a vehicle without a catalogue match carries the master-data fields as null, never omitted
    assert body["catalogueVariantId"] is None and body["listPrice"] is None and body["specification"] is None


def test_the_spec_block_is_a_plain_block_never_the_tenant_scoped_specification():
    """`specification` is the ADR-071 spec block (`VehicleSpecBlockRead`),
    not `CatalogueSpecificationRead` — the tenant-scoped options, colours,
    tyres and images (ADR-013) have no field here to regress into."""

    from app.vehicle.schemas.spec_block import VehicleSpecBlockRead

    annotation = SharedVehicleIdentity.model_fields["specification"].annotation
    assert VehicleSpecBlockRead in getattr(annotation, "__args__", (annotation,))
    block_fields = {VehicleSpecBlockRead.model_fields[n].alias or n for n in VehicleSpecBlockRead.model_fields}
    assert not (block_fields & {"options", "colours", "tyreSpecs", "images", "optionRelations"})

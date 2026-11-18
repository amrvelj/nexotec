"""ADR-112 (3), KAN-548 (closes KAN-130): the shared identity lookup of a
vehicle already known in the platform carries its catalogue variant, list
price and spec block for a dealer **without** a provider contract — master
data every dealer may read (ADR-075) — and never the tenant-scoped details
(options, colours, tyres, images; ADR-013), which stay behind
`GET /catalogue/variants/{id}/specification` on the dealer's own connection.
"""

import uuid
from decimal import Decimal

from app.core.auth import create_access_token
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(), roles=frozenset(), is_dealer_manager=False
    )


def _stored_variant(db_session) -> ModelVariant:
    brand = Brand(code="testmarke", display_name="Testmarke")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Prüfling")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="Prüfling 2.0 TDI", model_year_from=2021, vehicle_kind="passenger_car",
        fuel_type="diesel", ps=204, kw=150, doors=5, base_price=Decimal("48900.00"), base_price_year=2023, price_is_net=False,
    )
    db_session.add(variant)
    db_session.commit()
    return variant


def test_a_contract_less_dealer_reads_the_variant_price_and_spec_block_but_no_licensed_detail(client, db_session):
    variant = _stored_variant(db_session)
    create_vehicle_mdm(db_session, vin="ZAR94000007123457", catalogue_variant_id=variant.id)
    contract_less_tenant = uuid.uuid4()  # no integration connection at all

    response = client.get(
        "/v1/vehicle-mdm/lookup", params={"vin": "ZAR94000007123457"},
        headers={"Authorization": f"Bearer {_token(contract_less_tenant)}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["catalogueVariantId"] == str(variant.id)
    assert body["listPrice"] == {"amount": "48900.00", "year": 2023, "isNet": False}
    assert body["specification"]["ps"] == 204 and body["specification"]["kw"] == 150 and body["specification"]["doors"] == 5
    assert body["fuelType"] == "diesel"
    for licensed in ("options", "colours", "tyreSpecs", "images", "optionRelations"):
        assert licensed not in body and licensed not in body["specification"]

    # the tenant-scoped specification stays its own, connection-gated read
    detail = client.get(
        f"/v1/catalogue/variants/{variant.id}/specification",
        headers={"Authorization": f"Bearer {_token(contract_less_tenant)}"},
    )
    assert detail.status_code == 200 and detail.json()["hasProviderConnection"] is False
    assert detail.json()["options"] == [] and detail.json()["images"] == []

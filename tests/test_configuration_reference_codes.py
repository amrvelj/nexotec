"""KAN-96 — a configuration's five coded spec fields take only an *active*
value of their reference list.

`vehicleKind`, `fuelType`, `bodyStyle`, `drivetrain` and `transmission` on
`POST` / `PATCH /v1/configurations` are canonical `reference_value.value_code`
strings. A value that is not an active value of its list is a 422 naming
every offending field, the value and the list — the shape KAN-57 gave the
legacy `/v1/vehicles` (`tests/test_vehicle.py`), from the same function. A
list that does not exist at all is a deployment fault (500).

Anto's ruling (2026-10-07): a PATCH validates every coded value it carries,
including one sent back unchanged — a value retired from its list refuses
the save. Codes a provider configuration copies from its catalogue variant
are not re-validated at capture: their integrity belongs to catalogue sync
(KAN-75 / KAN-77).

Each test seeds the list it relies on and submits a value that is not in it
— never an unseeded list standing in for "not in the list".
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import VehicleConfiguration

_CODED_LISTS = ("vehicle_kind", "fuel_type", "body_style", "drivetrain", "transmission")


def _seed_list(db_session, list_code: str, *values: str, inactive: tuple[str, ...] = ()) -> None:
    ref_list = ReferenceList(list_code=list_code)
    db_session.add(ref_list)
    db_session.flush()
    for code, active in [*((v, True) for v in values), *((v, False) for v in inactive)]:
        db_session.add(
            ReferenceValue(
                list_id=ref_list.id, value_code=code, label_de=code, label_fr=code, label_it=code,
                label_en=code, active=active,
            )
        )
    db_session.commit()


def _seed_coded_lists(db_session) -> None:
    _seed_list(db_session, "vehicle_kind", "passenger_car", "motorcycle")
    _seed_list(db_session, "fuel_type", "petrol", "diesel", inactive=("hydrogen",))
    _seed_list(db_session, "body_style", "sedan", "estate")
    _seed_list(db_session, "drivetrain", "fwd", "awd")
    _seed_list(db_session, "transmission", "manual", "automatic")


def _variant(db_session, **coded) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="Golf GTI", model_year_from=2021, ps=245,
        base_price=Decimal("42500.00"), **coded,
    )
    db_session.add(variant)
    db_session.commit()
    return variant


def _headers(tenant_id: uuid.UUID, **extra: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _post(client, tenant_id: uuid.UUID, body: dict):
    return client.post(
        "/v1/configurations", json=body, headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())})
    )


def _manual(**coded) -> dict:
    return {"source": "manual", "mode": "record", "matchMethod": "manual", "brandDisplayName": "Citroën",
            "variantName": "2CV6", **coded}


def _configuration_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(VehicleConfiguration))


# --- create -------------------------------------------------------


def test_manual_create_stores_active_values_of_all_five_lists(client, db_session):
    _seed_coded_lists(db_session)

    r = _post(client, uuid.uuid4(), _manual(
        vehicleKind="passenger_car", fuelType="diesel", bodyStyle="estate", drivetrain="awd",
        transmission="automatic",
    ))

    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["vehicleKind"], body["fuelType"], body["bodyStyle"], body["drivetrain"], body["transmission"]) == (
        "passenger_car", "diesel", "estate", "awd", "automatic"
    )


def test_manual_create_with_bad_codes_is_one_422_naming_every_field_value_and_list(client, db_session):
    _seed_coded_lists(db_session)

    r = _post(client, uuid.uuid4(), _manual(fuelType="diesle", bodyStyle="spaceship", drivetrain="fwd"))

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["details"]["invalid"] == {"fuelType": "diesle", "bodyStyle": "spaceship"}
    assert "fuelType='diesle' (reference list 'fuel_type')" in error["message"]
    assert "bodyStyle='spaceship' (reference list 'body_style')" in error["message"]
    assert _configuration_count(db_session) == 0


def test_vehicle_kind_is_checked_against_the_vehicle_kind_list_not_the_legacy_vehicle_type(client, db_session):
    """`vehicle_kind` (PRD name, migration 6ba0a99ed5c4) and the legacy
    `vehicle_type` are separate lists. A code only the legacy list knows
    ('truck') is not a vehicle kind."""

    _seed_coded_lists(db_session)
    _seed_list(db_session, "vehicle_type", "passenger_car", "truck")

    r = _post(client, uuid.uuid4(), _manual(vehicleKind="truck"))

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"vehicleKind": "truck"}


def test_a_deactivated_value_is_refused_on_create(client, db_session):
    _seed_coded_lists(db_session)

    r = _post(client, uuid.uuid4(), _manual(fuelType="hydrogen"))

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"fuelType": "hydrogen"}


def test_a_missing_reference_list_is_a_deployment_fault_not_a_client_error(client, db_session):
    for list_code in _CODED_LISTS:
        if list_code != "transmission":
            _seed_list(db_session, list_code, "x")

    with pytest.raises(RuntimeError, match="'transmission' reference list is not seeded"):
        _post(client, uuid.uuid4(), _manual(transmission="manual"))


def test_provider_create_does_not_revalidate_codes_copied_from_the_catalogue(client, db_session):
    """Catalogue codes are catalogue sync's to keep honest (KAN-75 / KAN-77):
    a variant carrying a retired value and a value no list knows still
    captures."""

    _seed_coded_lists(db_session)
    variant = _variant(db_session, vehicle_kind="passenger_car", fuel_type="hydrogen", body_style="liftback")

    r = _post(client, uuid.uuid4(), {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })

    assert r.status_code == 201, r.text
    assert (r.json()["fuelType"], r.json()["bodyStyle"]) == ("hydrogen", "liftback")


# --- PATCH ----------------------------------------------------------


def test_patching_a_manual_configuration_to_a_bad_code_is_refused_and_changes_nothing(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, _manual(fuelType="petrol"))
    assert created.status_code == 201, created.text
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"fuelType": "diesle", "transmission": "sequential", "mileageKm": 1000},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"fuelType": "diesle", "transmission": "sequential"}
    after = client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()
    assert (after["fuelType"], after["transmission"], after["mileageKm"], after["version"]) == (
        "petrol", None, None, version
    )


def test_patching_a_manual_configuration_to_an_active_code_is_stored(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, _manual(fuelType="petrol"))
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"fuelType": "diesel", "vehicleKind": "motorcycle"},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 200, r.text
    assert (r.json()["fuelType"], r.json()["vehicleKind"]) == ("diesel", "motorcycle")


def test_patching_a_provider_configuration_coded_field_to_a_bad_code_is_refused(client, db_session):
    _seed_coded_lists(db_session)
    variant = _variant(db_session, fuel_type="petrol", drivetrain="fwd")
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"drivetrain": "4x4"},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"drivetrain": "4x4"}
    assert client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()["drivetrain"] == "fwd"


def test_a_patch_that_sends_a_retired_value_back_unchanged_is_refused(client, db_session):
    """Anto, 2026-10-07: every save uses current list values. A provider
    configuration captured 'hydrogen' from its variant; the admin has since
    retired it. A PATCH carrying it back — even unchanged, beside an
    unrelated edit — is a 422, and the unrelated edit is not applied."""

    _seed_coded_lists(db_session)
    variant = _variant(db_session, fuel_type="hydrogen")
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })
    assert created.status_code == 201, created.text
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"fuelType": "hydrogen", "mileageKm": 12000},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"fuelType": "hydrogen"}
    assert client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()["mileageKm"] is None


def test_a_patch_without_coded_fields_needs_no_reference_list(client, db_session):
    """A PATCH that carries no coded value validates nothing — the retired
    'hydrogen' already on the record does not block an unrelated edit."""

    _seed_coded_lists(db_session)
    variant = _variant(db_session, fuel_type="hydrogen")
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"mileageKm": 12000},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 200, r.text
    assert (r.json()["mileageKm"], r.json()["fuelType"]) == (12000, "hydrogen")

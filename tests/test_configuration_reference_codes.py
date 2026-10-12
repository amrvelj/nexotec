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
from tests.configuration_test_support import MANUAL_CODED_JSON

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


def _manual(**fields) -> dict:
    """A manual create body. All five coded fields are required on a manual
    configuration (Anto, 2026-10-12): the defaults are active values of the
    lists `_seed_coded_lists` seeds; a test overrides the ones it is about."""

    return {"source": "manual", "mode": "record", "matchMethod": "manual", "brandDisplayName": "Citroën",
            "variantName": "2CV6", **MANUAL_CODED_JSON, **fields}


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
        "petrol", "manual", None, version
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


# --- KAN-245: the spec block's five reference fields ---------------------
#
# `engineCycle`, `vehicleClass`, `valuationClassification`,
# `emissionStandard` and `consumptionNorm` inside `spec` are reference-list
# codes too (lists seeded by migrations a3d9c1e58f27 and 6ba0a99ed5c4; each
# list code is the field's own name). The KAN-96 rule applies unchanged: an
# active value of the list, or a 422 naming every bad field — coded and
# spec in one response; a missing list is a 500; a PATCH echoing a retired
# value is refused (Anto, 2026-10-12, R5); codes a provider configuration
# copied from its variant at capture are not re-validated.

_SPEC_LISTS = ("engine_cycle", "vehicle_class", "valuation_classification", "emission_standard", "consumption_norm")


def _seed_spec_lists(db_session) -> None:
    _seed_list(db_session, "engine_cycle", "four_stroke", inactive=("two_stroke",))
    _seed_list(db_session, "vehicle_class", "m1", "n1")
    _seed_list(db_session, "valuation_classification", "standard")
    _seed_list(db_session, "emission_standard", "euro_6d", inactive=("euro_4",))
    _seed_list(db_session, "consumption_norm", "wltp", "nedc")


def test_manual_create_stores_active_values_of_the_spec_reference_lists(client, db_session):
    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)

    r = _post(client, uuid.uuid4(), _manual(spec={
        "engineCycle": "four_stroke", "vehicleClass": "m1", "valuationClassification": "standard",
        "emissionStandard": "euro_6d", "consumptionNorm": "wltp",
    }))

    assert r.status_code == 201, r.text
    spec = r.json()["spec"]
    assert (spec["engineCycle"], spec["vehicleClass"], spec["valuationClassification"], spec["emissionStandard"],
            spec["consumptionNorm"]) == ("four_stroke", "m1", "standard", "euro_6d", "wltp")


def test_manual_create_with_bad_spec_codes_is_one_422_naming_every_field_value_and_list(client, db_session):
    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)

    r = _post(client, uuid.uuid4(), _manual(fuelType="diesle", spec={
        "engineCycle": "rotary", "vehicleClass": "m1", "valuationClassification": "exotic",
        "emissionStandard": "euro_4", "consumptionNorm": "epa", "ps": 29,
    }))

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["details"]["invalid"] == {
        "fuelType": "diesle", "engineCycle": "rotary", "valuationClassification": "exotic",
        "emissionStandard": "euro_4", "consumptionNorm": "epa",
    }
    assert "engineCycle='rotary' (reference list 'engine_cycle')" in error["message"]
    assert "valuationClassification='exotic' (reference list 'valuation_classification')" in error["message"]
    assert "emissionStandard='euro_4' (reference list 'emission_standard')" in error["message"]
    assert "consumptionNorm='epa' (reference list 'consumption_norm')" in error["message"]
    assert _configuration_count(db_session) == 0


def test_a_missing_spec_reference_list_is_a_deployment_fault(client, db_session):
    _seed_coded_lists(db_session)
    for list_code in _SPEC_LISTS:
        if list_code != "consumption_norm":
            _seed_list(db_session, list_code, "x")

    with pytest.raises(RuntimeError, match="'consumption_norm' reference list is not seeded"):
        _post(client, uuid.uuid4(), _manual(spec={"consumptionNorm": "wltp"}))


def test_patching_a_spec_reference_field_to_a_bad_code_is_refused_and_changes_nothing(client, db_session):
    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, _manual(spec={"vehicleClass": "m1"}))
    assert created.status_code == 201, created.text
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"spec": {"vehicleClass": "z9", "emissionStandard": "euro_7", "ps": 29}, "mileageKm": 1000},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"vehicleClass": "z9", "emissionStandard": "euro_7"}
    after = client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()
    assert (after["spec"]["vehicleClass"], after["spec"]["ps"], after["mileageKm"], after["version"]) == (
        "m1", None, None, version
    )


def test_patching_a_spec_reference_field_to_an_active_code_is_stored(client, db_session):
    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, _manual(spec={"vehicleClass": "m1"}))
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"spec": {"vehicleClass": "n1", "consumptionNorm": "nedc"}},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 200, r.text
    assert (r.json()["spec"]["vehicleClass"], r.json()["spec"]["consumptionNorm"]) == ("n1", "nedc")


def test_a_patch_echoing_a_retired_spec_value_is_refused(client, db_session):
    """R5 — the KAN-96 ruling for coded fields holds for the spec block: a
    provider configuration captured 'euro_4' from its variant (not checked
    at capture); the list has retired it since. A PATCH carrying it back,
    unchanged, beside an unrelated edit, is a 422 and the edit is not
    applied."""

    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)
    variant = _variant(db_session, emission_standard="euro_4", engine_cycle="two_stroke")
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })
    assert created.status_code == 201, created.text
    assert created.json()["spec"]["emissionStandard"] == "euro_4"
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"spec": {"emissionStandard": "euro_4"}, "mileageKm": 12000},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["invalid"] == {"emissionStandard": "euro_4"}
    assert client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()["mileageKm"] is None


def test_clearing_a_spec_reference_field_needs_no_list_value(client, db_session):
    _seed_coded_lists(db_session)
    _seed_spec_lists(db_session)
    variant = _variant(db_session, engine_cycle="two_stroke")
    tenant_id = uuid.uuid4()
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "build", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(variant.id),
    })
    cid, version = created.json()["id"], created.json()["version"]

    r = client.patch(
        f"/v1/configurations/{cid}",
        json={"spec": {"engineCycle": None}},
        headers=_headers(tenant_id, **{"If-Match": str(version)}),
    )

    assert r.status_code == 200, r.text
    assert r.json()["spec"]["engineCycle"] is None

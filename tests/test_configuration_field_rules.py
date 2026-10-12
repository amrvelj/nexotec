"""KAN-246 / KAN-247 — what a `PATCH /v1/configurations/{id}` may change,
clear and must record, under Anto's rulings of 2026-10-12.

R1 — the five coded fields (`vehicleKind`, `fuelType`, `bodyStyle`,
`drivetrain`, `transmission`):

* on a provider configuration (`source = provider`, as the overlay decides
  it) they are the catalogue's and locked: a PATCH that *changes* one is a 422
  `coded_field_locked` naming each field; the same value sent back is a
  no-op. FR-C-16's re-sync still updates them (not this endpoint).
* on a manual configuration all five are required at create (422
  `coded_fields_required` naming each missing one) and none can be cleared
  afterwards (explicit null → 422).

R2 — every other nullable field can be cleared: a PATCH tells an absent
field ("leave alone") from an explicit null ("clear"), top level and spec
block alike.

R3 — every change to a coded field is in the audit before/after exactly like
a spec-block field; an unchanged value records nothing.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.configuration import VehicleConfiguration
from tests.configuration_test_support import MANUAL_CODED_JSON, seed_reference_list

_CODED_CAMEL = ("vehicleKind", "fuelType", "bodyStyle", "drivetrain", "transmission")


def _seed_coded_lists(db_session) -> None:
    seed_reference_list(db_session, "vehicle_kind", "passenger_car", "motorcycle")
    seed_reference_list(db_session, "fuel_type", "petrol", "diesel")
    seed_reference_list(db_session, "body_style", "sedan", "estate")
    seed_reference_list(db_session, "drivetrain", "fwd", "awd")
    seed_reference_list(db_session, "transmission", "manual", "automatic")


def _variant(db_session) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db_session.add(brand)
    db_session.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db_session.add(group)
    db_session.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="Golf GTI", model_year_from=2021, ps=245, base_price=Decimal("42500.00"),
        vehicle_kind="passenger_car", fuel_type="petrol", body_style="sedan", drivetrain="fwd",
        transmission="automatic",
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


def _patch(client, tenant_id: uuid.UUID, cid: str, version: int, body: dict):
    return client.patch(f"/v1/configurations/{cid}", json=body, headers=_headers(tenant_id, **{"If-Match": str(version)}))


def _get(client, tenant_id: uuid.UUID, cid: str) -> dict:
    return client.get(f"/v1/configurations/{cid}", headers=_headers(tenant_id)).json()


def _provider(client, db_session, tenant_id: uuid.UUID) -> dict:
    created = _post(client, tenant_id, {
        "source": "provider", "mode": "record", "matchMethod": "catalogue_browse",
        "catalogueVariantId": str(_variant(db_session).id),
    })
    assert created.status_code == 201, created.text
    return created.json()


def _manual(client, tenant_id: uuid.UUID, **extra) -> dict:
    created = _post(client, tenant_id, {
        "source": "manual", "mode": "record", "matchMethod": "manual", "brandDisplayName": "Citroën",
        "variantName": "2CV6", **MANUAL_CODED_JSON, **extra,
    })
    assert created.status_code == 201, created.text
    return created.json()


def _update_audits(db_session, cid: str) -> list[AuditEvent]:
    return list(db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.entity_type == "vehicle_configuration",
            AuditEvent.entity_id == uuid.UUID(cid),
            AuditEvent.action == "update",
        )
    ).all())


# --- R1: provider configurations — coded fields locked ------------------


def test_changing_a_coded_field_on_a_provider_configuration_is_refused_and_changes_nothing(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _provider(client, db_session, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"],
               {"fuelType": "diesel", "drivetrain": "awd", "mileageKm": 12000})

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["details"]["reason"] == "coded_field_locked"
    assert error["details"]["fields"] == ["fuelType", "drivetrain"]
    after = _get(client, tenant_id, created["id"])
    assert (after["fuelType"], after["drivetrain"], after["mileageKm"], after["version"]) == (
        "petrol", "fwd", None, created["version"]
    )
    assert _update_audits(db_session, created["id"]) == []


def test_clearing_a_coded_field_on_a_provider_configuration_is_refused(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _provider(client, db_session, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"], {"transmission": None})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"] == {"reason": "coded_field_locked", "fields": ["transmission"]}
    assert _get(client, tenant_id, created["id"])["transmission"] == "automatic"


def test_a_provider_configurations_coded_values_sent_back_unchanged_are_accepted(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _provider(client, db_session, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"], {
        "vehicleKind": "passenger_car", "fuelType": "petrol", "bodyStyle": "sedan", "drivetrain": "fwd",
        "transmission": "automatic", "mileageKm": 12000,
    })

    assert r.status_code == 200, r.text
    assert (r.json()["fuelType"], r.json()["mileageKm"]) == ("petrol", 12000)
    assert r.json()["overriddenFields"] == []
    # nothing coded changed, so nothing coded is audited
    assert _update_audits(db_session, created["id"]) == []


# --- R1: manual configurations — all five required, none clearable ------


def test_a_manual_create_without_the_coded_fields_is_one_422_naming_each_missing_field(client, db_session):
    _seed_coded_lists(db_session)

    r = _post(client, uuid.uuid4(), {
        "source": "manual", "mode": "record", "matchMethod": "manual", "brandDisplayName": "Citroën",
    })

    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["details"] == {"reason": "coded_fields_required", "missing": list(_CODED_CAMEL)}
    for field in _CODED_CAMEL:
        assert field in error["message"]
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == 0


def test_a_manual_create_names_only_the_coded_fields_it_lacks(client, db_session):
    _seed_coded_lists(db_session)

    r = _post(client, uuid.uuid4(), {
        "source": "manual", "mode": "record", "matchMethod": "manual",
        **MANUAL_CODED_JSON, "bodyStyle": None, "transmission": None,
    })

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"]["missing"] == ["bodyStyle", "transmission"]


def test_clearing_a_coded_field_on_a_manual_configuration_is_refused_and_changes_nothing(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"],
               {"fuelType": None, "bodyStyle": None, "mileageKm": 5000})

    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"] == {"reason": "coded_field_required", "fields": ["fuelType", "bodyStyle"]}
    after = _get(client, tenant_id, created["id"])
    assert (after["fuelType"], after["bodyStyle"], after["mileageKm"], after["version"]) == (
        "petrol", "sedan", None, created["version"]
    )


def test_a_manual_configuration_linked_to_a_variant_keeps_its_coded_fields_editable(client, db_session):
    """The lock follows `source`, as the overlay decides it — a manual
    configuration that carries a catalogue variant is still the advisor's to
    change (KAN-246 review). The variant is set on the row directly here."""

    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id)
    row = db_session.get(VehicleConfiguration, uuid.UUID(created["id"]))
    row.catalogue_variant_id = _variant(db_session).id
    db_session.commit()

    r = _patch(client, tenant_id, created["id"], created["version"], {"fuelType": "diesel"})

    assert r.status_code == 200, r.text
    assert _get(client, tenant_id, created["id"])["fuelType"] == "diesel"


def test_a_patch_linking_a_manual_configuration_to_a_variant_may_change_a_coded_field_too(client, db_session):
    """The other half of the old condition: a PATCH that links a variant and
    changes a coded field in one request is not refused on a manual one."""

    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id)
    variant_id = str(_variant(db_session).id)

    r = _patch(client, tenant_id, created["id"], created["version"],
               {"catalogueVariantId": variant_id, "fuelType": "diesel"})

    assert r.status_code == 200, r.text
    assert _get(client, tenant_id, created["id"])["fuelType"] == "diesel"


# --- R3: coded-field changes are audited like a spec field --------------


def test_a_coded_field_change_on_a_manual_configuration_is_in_the_audit_before_and_after(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"], {
        "fuelType": "diesel", "drivetrain": "fwd", "spec": {"ps": 29},
    })

    assert r.status_code == 200, r.text
    assert r.json()["fuelType"] == "diesel"
    (audit,) = _update_audits(db_session, created["id"])
    # drivetrain was sent unchanged: not recorded
    assert audit.before == {"fuel_type": "petrol", "ps": None}
    assert audit.after == {"fuel_type": "diesel", "ps": 29}


def test_an_unchanged_coded_value_on_a_manual_configuration_records_nothing(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id)

    r = _patch(client, tenant_id, created["id"], created["version"], {**MANUAL_CODED_JSON})

    assert r.status_code == 200, r.text
    assert _update_audits(db_session, created["id"]) == []


# --- R2: absent leaves alone, explicit null clears ----------------------


def test_clearing_top_level_fields_with_explicit_null_persists(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(
        client, tenant_id, vin="VF7AAAAAAAA000001", firstRegistrationDate="1985-04-01", mileageKm=142000,
        exteriorColour="Rouge", interiorColour="Gris", exteriorColourSurcharge="500.00", wheels="Steel",
        wheelsSurcharge="100.00", notes="Garage find", licencePlate="ZH 1234", stammnummer="123456789",
        typeApprovalNumber="1AB234", modelGroupName="2CV",
    )

    cleared = {
        "vin": None, "firstRegistrationDate": None, "mileageKm": None, "exteriorColour": None,
        "interiorColour": None, "exteriorColourSurcharge": None, "wheels": None, "wheelsSurcharge": None,
        "notes": None, "licencePlate": None, "stammnummer": None, "typeApprovalNumber": None,
        "modelGroupName": None,
    }
    r = _patch(client, tenant_id, created["id"], created["version"], cleared)

    assert r.status_code == 200, r.text
    after = _get(client, tenant_id, created["id"])
    for field in cleared:
        assert after[field] is None, field
    # not sent → untouched
    assert (after["brandDisplayName"], after["variantName"], after["fuelType"]) == ("Citroën", "2CV6", "petrol")


def test_clearing_a_spec_field_with_explicit_null_persists_and_is_audited(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id, spec={"ps": 29, "displacementCcm": 602})

    r = _patch(client, tenant_id, created["id"], created["version"], {"spec": {"ps": None}})

    assert r.status_code == 200, r.text
    after = _get(client, tenant_id, created["id"])
    assert (after["spec"]["ps"], after["spec"]["displacementCcm"]) == (None, 602)
    (audit,) = _update_audits(db_session, created["id"])
    assert (audit.before, audit.after) == ({"ps": 29}, {"ps": None})


def test_clearing_a_field_on_a_provider_configuration_persists_and_marks_a_spec_field_overridden(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _provider(client, db_session, tenant_id)
    set_up = _patch(client, tenant_id, created["id"], created["version"], {"mileageKm": 30000, "exteriorColour": "Black"})
    assert set_up.status_code == 200, set_up.text

    r = _patch(client, tenant_id, created["id"], set_up.json()["version"],
               {"mileageKm": None, "exteriorColour": None, "spec": {"ps": None}})

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["mileageKm"], body["exteriorColour"], body["spec"]["ps"]) == (None, None, None)
    assert body["overriddenFields"] == ["ps"]


def test_omitting_a_field_changes_nothing(client, db_session):
    _seed_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _manual(client, tenant_id, mileageKm=142000, exteriorColour="Rouge", spec={"ps": 29})

    r = _patch(client, tenant_id, created["id"], created["version"], {"notes": "Re-checked", "spec": {}})

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["mileageKm"], body["exteriorColour"], body["spec"]["ps"], body["notes"]) == (
        142000, "Rouge", 29, "Re-checked"
    )
    assert body["fuelType"] == "petrol"

"""KAN-449 — a valuation built from a configuration takes the
configuration's VIN (Anto, 2026-10-25). Before, a `vin` sent beside a
`configurationId` was stored as sent while `vehicleId` stayed the
configuration's: the valuation named one car by VIN and another by id. Now
a differing VIN is a 422 that stores nothing, the same VIN in any spelling is
accepted, and the nightly reconciliation reports a row left disagreeing
(rule 10)."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.reconciliation import ReconciliationAlarm
from app.valuation import reconciliation as valuation_reconciliation
from app.valuation.models.valuation import Valuation
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.public import create_or_get_vehicle_mdm
from tests.configuration_test_support import MANUAL_CODED_JSON, seed_manual_coded_lists

CONFIGURATION_VIN = "WVWZZZ1KZAW449001"
OTHER_VIN = "WVWZZZ1KZAW449002"
VIN_DIFFERS = "valuation.vehicle_vin differs from its vehicle_mdm.vin"


def _headers(tenant_id: uuid.UUID, **extra: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _configuration_of_a_known_car(client, db_session, tenant_id: uuid.UUID) -> dict:
    seed_manual_coded_lists(db_session)
    vehicle, _ = create_or_get_vehicle_mdm(db_session, vin=CONFIGURATION_VIN, actor_id=uuid.uuid4())
    db_session.commit()
    response = client.post(
        "/v1/configurations",
        json={
            "source": "manual", "mode": "record", "matchMethod": "manual", **MANUAL_CODED_JSON,
            "vin": CONFIGURATION_VIN,
        },
        headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert response.status_code == 201, response.text
    assert response.json()["vehicleId"] == str(vehicle.id)
    return response.json()


def _post_valuation(client, tenant_id: uuid.UUID, configuration_id: str, **fields):
    return client.post(
        "/v1/valuations",
        # KAN-574: a valuation names its car; these configurations carry no
        # make or model of their own, so the request names them.
        json={
            "configurationId": configuration_id, "vehicleMake": "VW", "vehicleModel": "Golf",
            "finalOffer": "18000.00", "source": "manual", **fields,
        },
        headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())}),
    )


def _valuation_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Valuation))


def test_a_vin_that_differs_from_the_configurations_is_422_and_stores_nothing(client, db_session):
    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)

    response = _post_valuation(client, tenant_id, configuration["id"], vin=OTHER_VIN)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "vin", "reason": "vin_differs_from_configuration"}
    assert _valuation_count(db_session) == 0


def test_a_vin_beside_a_configuration_without_one_is_stored_and_links_no_car(client, db_session):
    """A configuration with no VIN links no car (the link follows the VIN,
    KAN-326), so a VIN typed beside it can disagree with nothing: it is
    stored, and like every configuration path it finds or creates no
    vehicle-mdm record (ADR-070) — even one that holds this VIN."""

    seed_manual_coded_lists(db_session)
    create_or_get_vehicle_mdm(db_session, vin=OTHER_VIN, actor_id=uuid.uuid4())
    db_session.commit()
    tenant_id = uuid.uuid4()
    created = client.post(
        "/v1/configurations",
        json={"source": "manual", "mode": "record", "matchMethod": "manual", **MANUAL_CODED_JSON},
        headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert created.status_code == 201, created.text
    assert created.json()["vehicleId"] is None

    response = _post_valuation(client, tenant_id, created.json()["id"], vin=OTHER_VIN.lower())

    assert response.status_code == 201, response.text
    assert (response.json()["vehicleVin"], response.json()["vehicleId"]) == (OTHER_VIN, None)


def test_a_configuration_linked_to_a_car_without_a_stored_vin_refuses_any_vin(client, db_session):
    """A configuration that links a car names it even with its VIN missing
    (no writer leaves that shape today; the guard does not rely on it): any
    VIN sent beside it is refused, its own included. With none sent the
    valuation used to take the configuration's car and no VIN; since KAN-574
    (Anto, 2026-11-07, option A) a valuation names its car by its VIN, so
    that is refused too (`valuation_names_no_car`) — the configuration's VIN
    is repaired first."""

    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)
    stored = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    stored.vin = None
    db_session.commit()

    for vin in (OTHER_VIN, CONFIGURATION_VIN):
        refused = _post_valuation(client, tenant_id, configuration["id"], vin=vin)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["details"]["reason"] == "vin_differs_from_configuration"
    assert _valuation_count(db_session) == 0

    refused = _post_valuation(client, tenant_id, configuration["id"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "valuation_names_no_car", "fields": ["vin"]}
    assert _valuation_count(db_session) == 0


def test_the_configurations_own_vin_in_any_spelling_is_accepted(client, db_session):
    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)

    response = _post_valuation(client, tenant_id, configuration["id"], vin="wvwzzz1kzaw 449001")

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["vehicleVin"], body["vehicleId"]) == (CONFIGURATION_VIN, configuration["vehicleId"])


def test_no_vin_takes_the_configurations(client, db_session):
    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)

    response = _post_valuation(client, tenant_id, configuration["id"])

    assert response.status_code == 201, response.text
    assert (response.json()["vehicleVin"], response.json()["vehicleId"]) == (CONFIGURATION_VIN, configuration["vehicleId"])


def test_a_configuration_vin_stored_unnormalised_still_matches_its_normalised_spelling(client, db_session):
    """A configuration VIN from before KAN-338 may still be lower case
    (scripts/repair_configuration_vins.py has not run everywhere); the
    request's VIN is normalised, so the comparison normalises both."""

    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)
    stored = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    stored.vin = CONFIGURATION_VIN.lower()
    db_session.commit()

    response = _post_valuation(client, tenant_id, configuration["id"], vin=CONFIGURATION_VIN)

    assert response.status_code == 201, response.text
    assert response.json()["vehicleVin"] == CONFIGURATION_VIN


def test_a_configuration_vin_stored_unnormalised_is_taken_normalised_when_none_is_sent(client, db_session):
    """The reviewer's regression: the dialog sends no VIN beside a
    configuration, so the configuration's own VIN is the one judged and
    stored — normalised, one spelling (FR-V-15), never refused for the case
    it was stored in before KAN-338."""

    tenant_id = uuid.uuid4()
    configuration = _configuration_of_a_known_car(client, db_session, tenant_id)
    stored = db_session.get(VehicleConfiguration, uuid.UUID(configuration["id"]))
    stored.vin = CONFIGURATION_VIN.lower()
    db_session.commit()

    response = _post_valuation(client, tenant_id, configuration["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["vehicleVin"], body["vehicleId"]) == (CONFIGURATION_VIN, configuration["vehicleId"])


# --- rule 10: a row left disagreeing is reported -------------------------------


def _findings(db_session) -> list:
    try:
        valuation_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return [orphan for orphan in alarm.orphans if orphan.check_label == VIN_DIFFERS]
    return []


def _stored_valuation(db_session, *, vin: str | None, vehicle_id: uuid.UUID | None) -> Valuation:
    valuation = Valuation(
        tenant_id=uuid.uuid4(), valuation_number=f"B-{uuid.uuid4().hex[:6]}", vehicle_id=vehicle_id,
        vehicle_vin=vin, source="manual", final_offer=Decimal("18000.00"),
        valid_from=utcnow().date(), valid_until=utcnow() + dt.timedelta(days=30),
    )
    db_session.add(valuation)
    db_session.commit()
    return valuation


def test_reconciliation_reports_a_valuation_whose_vin_is_not_its_vehicles(db_session):
    vehicle, _ = create_or_get_vehicle_mdm(db_session, vin=CONFIGURATION_VIN, actor_id=uuid.uuid4())
    db_session.commit()
    disagreeing = _stored_valuation(db_session, vin=OTHER_VIN, vehicle_id=vehicle.id)
    _stored_valuation(db_session, vin=CONFIGURATION_VIN, vehicle_id=vehicle.id)
    _stored_valuation(db_session, vin=CONFIGURATION_VIN.lower(), vehicle_id=vehicle.id)  # a spelling, not another car
    _stored_valuation(db_session, vin=None, vehicle_id=vehicle.id)
    _stored_valuation(db_session, vin=OTHER_VIN, vehicle_id=None)

    findings = _findings(db_session)

    assert [finding.source_row_id for finding in findings] == [disagreeing.id]

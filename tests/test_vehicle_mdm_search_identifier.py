"""KAN-82: an identifier typed into the one search box (FR-V-06) answers
with the vehicle it names — or the picker, or nothing — and never with a
page of other vehicles alongside. Global search (FR-UI-08) renders
`filtered` whenever no hit or picker came back, so an unrelated page there
shows the wrong cars to someone reading a plate off a car at the counter.
"""

import datetime as dt
import uuid

from app.core.auth import create_access_token
from app.vehicle.services.plate import record_plate_assignment
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

TENANT_ID = uuid.uuid4()
# Plates valid from well before today, open-ended: current whenever the
# suite runs (the endpoint resolves against plates valid today).
SINCE = dt.date(2020, 1, 1)

TARGET_VIN = "ZAR94000007123456"
PAIR_VIN = "WVWZZZ1JZXW000001"
UNRELATED_VINS = ["1HGCM82633A004352", "WBA3A5C51CF256985", "VF1RFB00X57123456"]


def _bearer() -> dict[str, str]:
    tid = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _plate(db_session, vehicle_id, plate, *, valid_to=None, group_id=None, valid_from=SINCE):
    record_plate_assignment(
        db_session, vehicle_id=vehicle_id, plate=plate, canton=plate.split()[0], valid_from=valid_from,
        valid_to=valid_to, is_interchangeable=group_id is not None, plate_group_id=group_id,
        recording_tenant_id=TENANT_ID,
    )


def _seed(db_session):
    """The target car plus three unrelated ones — what the old code put
    in `filtered` next to every resolved match."""

    target = create_vehicle_mdm(db_session, vin=TARGET_VIN, catalogue_variant_id=None)
    for vin in UNRELATED_VINS:
        create_vehicle_mdm(db_session, vin=vin, catalogue_variant_id=None)
    return target


def _assert_empty_page(filtered: dict) -> None:
    assert filtered == {"items": [], "nextCursor": None, "total": 0, "totalIsEstimate": False}


def test_a_resolved_plate_carries_no_unrelated_page_and_names_its_current_plate(client, db_session):
    target = _seed(db_session)
    _plate(db_session, target.id, "ZH 99001", valid_to=dt.date(2021, 1, 1))
    _plate(db_session, target.id, "ZH 12345", valid_from=dt.date(2021, 1, 2))
    db_session.commit()

    body = client.get("/v1/vehicle-mdm/search?q=ZH 12345", headers=_bearer()).json()

    assert body["resolved"]["id"] == str(target.id)
    assert body["resolved"]["currentPlate"] == "ZH 12345"
    assert body["pickerCandidates"] == []
    _assert_empty_page(body["filtered"])


def test_a_resolved_vin_names_the_plate_valid_today_not_an_expired_one(client, db_session):
    target = _seed(db_session)
    _plate(db_session, target.id, "BE 4711", valid_to=dt.date(2021, 1, 1))
    db_session.commit()

    body = client.get(f"/v1/vehicle-mdm/search?q={TARGET_VIN}", headers=_bearer()).json()

    assert body["resolved"]["id"] == str(target.id)
    assert body["resolved"]["currentPlate"] is None
    _assert_empty_page(body["filtered"])


def test_a_wechselschild_plate_answers_with_the_picker_only(client, db_session):
    target = _seed(db_session)
    pair = create_vehicle_mdm(db_session, vin=PAIR_VIN, catalogue_variant_id=None)
    group_id = uuid.uuid4()
    _plate(db_session, target.id, "TG 41277", group_id=group_id)
    _plate(db_session, pair.id, "TG 41277", group_id=group_id)
    db_session.commit()

    body = client.get("/v1/vehicle-mdm/search?q=TG 41277", headers=_bearer()).json()

    assert body["resolved"] is None
    assert {c["id"] for c in body["pickerCandidates"]} == {str(target.id), str(pair.id)}
    assert all(not c["isConflict"] for c in body["pickerCandidates"])
    _assert_empty_page(body["filtered"])


def test_a_real_identifier_that_matches_nothing_filters_to_nothing(client, db_session):
    _seed(db_session)
    db_session.commit()

    for q in ["ZZZZZZZZZZZZZZZZZ", "AG 55555", "F-999999", "999999999"]:
        body = client.get(f"/v1/vehicle-mdm/search?q={q}", headers=_bearer()).json()
        assert body["resolved"] is None, q
        assert body["pickerCandidates"] == [], q
        _assert_empty_page(body["filtered"])


def test_a_brand_fragment_still_filters_and_an_empty_query_still_lists(client, db_session):
    _seed(db_session)
    db_session.commit()

    listed = client.get("/v1/vehicle-mdm/search?q=", headers=_bearer()).json()
    assert len(listed["filtered"]["items"]) == 1 + len(UNRELATED_VINS)
    assert listed["resolved"] is None

    fragment = client.get("/v1/vehicle-mdm/search?q=WBA3A5", headers=_bearer()).json()
    assert fragment["resolved"] is None
    assert [v["vin"] for v in fragment["filtered"]["items"]] == ["WBA3A5C51CF256985"]


def test_a_half_typed_vehicle_number_or_vin_prefix_still_filters(client, db_session):
    """`F-0001` and `VF1` are plate-shaped (`_PLATE_RE` is loose on
    purpose), so they are tried as identifiers first; finding no plate,
    they must still filter — never a false "no match" while matching cars
    exist (KAN-82 review)."""

    target = _seed(db_session)
    db_session.commit()

    by_number = client.get(f"/v1/vehicle-mdm/search?q={target.vehicle_number[:6]}", headers=_bearer()).json()
    assert by_number["resolved"] is None
    assert str(target.id) in {v["id"] for v in by_number["filtered"]["items"]}

    by_vin_prefix = client.get("/v1/vehicle-mdm/search?q=VF1", headers=_bearer()).json()
    assert by_vin_prefix["resolved"] is None
    assert [v["vin"] for v in by_vin_prefix["filtered"]["items"]] == ["VF1RFB00X57123456"]

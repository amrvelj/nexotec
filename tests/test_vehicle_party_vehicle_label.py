"""KAN-84 — VehicleParty carries the vehicle's label (CLAUDE.md rule 2)
instead of joining to vehicle_mdm through a cross-context relationship().

What is pinned here:
- every write that opens or repoints a party row stores the label (VIN,
  vehicle number, make, model, year, trim) and stamps
  vehicle_label_refreshed_at;
- the customer's Vehicles tab reads the stored label, not the live vehicle —
  a later catalogue match shows only once the nightly refresh has run;
- the nightly refresh relabels what changed (moving updated_at), stamps
  what did not (holding updated_at), backfills rows written before KAN-84,
  and skips a dangling vehicle id without failing;
- a row with no label yet is filled for a read without a write, and gets
  its label written by the next write to it;
- the worker registers the job by name.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import worker
from app.core.auth import AccessRole
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.services.customer import refresh_vehicle_party_labels, repoint_vehicle_party
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant
from app.vehicle.models.vehicle_mdm import VehicleMdm
from tests.test_customer_vehicle import _bearer, _create_customer, _create_dealer, _create_vehicle, _token


def _variant(db) -> ModelVariant:
    brand = Brand(code=f"b-{uuid.uuid4().hex[:6]}", display_name="Volkswagen")
    db.add(brand)
    db.flush()
    group = ModelGroup(brand_id=brand.id, name="Golf")
    db.add(group)
    db.flush()
    variant = ModelVariant(
        model_group_id=group.id, name="2.0 TSI GTI", model_year_from=2021, vehicle_kind="passenger_car",
        fuel_type="petrol", ps=245, base_price=Decimal("42500.00"),
    )
    db.add(variant)
    db.commit()
    return variant


def _link(client, dealer_id: str, customer_id: str, vehicle_id: str, role: str = "owner") -> dict:
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer_id}/vehicles", json={"vehicleId": vehicle_id, "role": role}, headers=_bearer(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _list(client, dealer_id: str, customer_id: str) -> list[dict]:
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))
    response = client.get(f"/v1/customers/{customer_id}/vehicles", headers=_bearer(token))
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _party(db, party_id) -> VehicleParty:
    db.expire_all()
    return db.scalar(select(VehicleParty).where(VehicleParty.id == uuid.UUID(str(party_id))))


def _match(db, vehicle_id: str, variant: ModelVariant, first_registration: dt.date | None = None) -> None:
    """A catalogue match (and registration date) arriving AFTER the link —
    written straight onto vehicle_mdm, as the vehicle context would."""

    vehicle = db.get(VehicleMdm, uuid.UUID(vehicle_id))
    vehicle.catalogue_variant_id = variant.id
    if first_registration is not None:
        vehicle.first_registration_date = first_registration
    db.commit()


def _setup(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    vehicle = _create_vehicle(client, dealer_id)
    return dealer_id, customer, vehicle


# --- writes store the label -------------------------------------------------------------


def test_linking_stores_the_vehicle_label_on_the_party_row(client, db_session):
    dealer_id, customer, vehicle = _setup(client)
    _match(db_session, vehicle["id"], _variant(db_session), dt.date(2022, 3, 1))

    body = _link(client, dealer_id, customer["id"], vehicle["id"])

    row = _party(db_session, body["id"])
    assert (row.vehicle_vin, row.vehicle_number) == (vehicle["vin"], vehicle["vehicleNumber"])
    assert (row.vehicle_make, row.vehicle_model, row.vehicle_trim, row.vehicle_model_year) == (
        "Volkswagen", "Golf", "2.0 TSI GTI", 2022,
    )
    assert row.vehicle_label_refreshed_at is not None
    assert body["vehicle"] == {
        "id": vehicle["id"], "vin": vehicle["vin"], "vehicleNumber": vehicle["vehicleNumber"],
        "make": "Volkswagen", "model": "Golf", "modelYear": 2022, "trim": "2.0 TSI GTI",
    }


def test_a_backdated_closed_create_also_stores_the_label(client, db_session):
    dealer_id, customer, vehicle = _setup(client)
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))
    response = client.post(
        f"/v1/customers/{customer['id']}/vehicles",
        json={
            "vehicleId": vehicle["id"], "role": "keeper",
            "effectiveFrom": "2025-01-01T00:00:00Z", "effectiveTo": "2025-06-01T00:00:00Z",
        },
        headers=_bearer(token),
    )
    assert response.status_code == 201, response.text
    row = _party(db_session, response.json()["id"])
    assert row.vehicle_vin == vehicle["vin"]
    assert row.vehicle_label_refreshed_at is not None


def test_a_vehicle_merge_relabels_repointed_parties_with_the_survivor(client, db_session):
    dealer_id, customer, duplicate = _setup(client)
    survivor = _create_vehicle(client, dealer_id)
    body = _link(client, dealer_id, customer["id"], duplicate["id"])

    assert repoint_vehicle_party(
        db_session, duplicate_vehicle_id=uuid.UUID(duplicate["id"]), survivor_vehicle_id=uuid.UUID(survivor["id"])
    ) == 1

    row = _party(db_session, body["id"])
    assert row.vehicle_id == uuid.UUID(survivor["id"])
    assert (row.vehicle_vin, row.vehicle_number) == (survivor["vin"], survivor["vehicleNumber"])


# --- reads use the stored label, the nightly job refreshes it ---------------------------


def test_the_vehicles_tab_reads_the_stored_label_until_the_nightly_refresh(client, db_session):
    """The proof the read no longer joins: a match made after the link is
    invisible until refresh_vehicle_party_labels has run."""

    dealer_id, customer, vehicle = _setup(client)
    _link(client, dealer_id, customer["id"], vehicle["id"])
    _match(db_session, vehicle["id"], _variant(db_session))

    assert _list(client, dealer_id, customer["id"])[0]["vehicle"]["make"] is None

    assert refresh_vehicle_party_labels(db_session) == 1

    listed = _list(client, dealer_id, customer["id"])[0]["vehicle"]
    assert (listed["make"], listed["model"], listed["trim"]) == ("Volkswagen", "Golf", "2.0 TSI GTI")


def test_refresh_moves_updated_at_only_where_the_label_changed(client, db_session):
    dealer_id, customer, changing = _setup(client)
    steady = _create_vehicle(client, dealer_id)
    changing_party = _link(client, dealer_id, customer["id"], changing["id"], role="owner")
    steady_party = _link(client, dealer_id, customer["id"], steady["id"], role="keeper")
    _match(db_session, changing["id"], _variant(db_session))
    before_changing, before_steady = _party(db_session, changing_party["id"]), _party(db_session, steady_party["id"])
    changing_updated, steady_updated = before_changing.updated_at, before_steady.updated_at
    steady_refreshed = before_steady.vehicle_label_refreshed_at

    assert refresh_vehicle_party_labels(db_session) == 1

    after_changing, after_steady = _party(db_session, changing_party["id"]), _party(db_session, steady_party["id"])
    assert after_changing.vehicle_make == "Volkswagen"
    assert after_changing.updated_at > changing_updated
    assert after_steady.updated_at == steady_updated  # nothing a reader sees changed
    assert after_steady.vehicle_label_refreshed_at > steady_refreshed  # but it was checked


def test_refresh_backfills_unlabelled_rows_and_skips_a_dangling_vehicle(client, db_session):
    """Rows written before KAN-84 have no label; the job is their backfill.
    Verified by query, per the migrations rule."""

    _dealer_id, customer, vehicle = _setup(client)
    legacy = VehicleParty(vehicle_id=uuid.UUID(vehicle["id"]), customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.OWNER)
    dangling = VehicleParty(vehicle_id=uuid.uuid4(), customer_id=uuid.UUID(customer["id"]), role=VehiclePartyRole.DRIVER)
    db_session.add_all([legacy, dangling])
    db_session.commit()
    legacy_id, dangling_id = legacy.id, dangling.id

    assert refresh_vehicle_party_labels(db_session) == 1

    assert _party(db_session, legacy_id).vehicle_vin == vehicle["vin"]
    assert _party(db_session, legacy_id).vehicle_label_refreshed_at is not None
    dangling_row = _party(db_session, dangling_id)
    assert dangling_row.vehicle_vin is None and dangling_row.vehicle_label_refreshed_at is None
    unlabelled = db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_vin.is_(None))).all()
    assert [p.id for p in unlabelled] == [dangling_id]


def test_refresh_pages_past_one_batch(client, db_session, monkeypatch):
    from app.customer.services import customer as customer_service

    monkeypatch.setattr(customer_service, "_LABEL_REFRESH_BATCH", 2)
    _dealer_id, customer, vehicle = _setup(client)
    for role in VehiclePartyRole:
        db_session.add(VehicleParty(vehicle_id=uuid.UUID(vehicle["id"]), customer_id=uuid.UUID(customer["id"]), role=role))
    db_session.commit()

    assert refresh_vehicle_party_labels(db_session) == len(VehiclePartyRole)
    db_session.expire_all()
    assert db_session.scalars(select(VehicleParty).where(VehicleParty.vehicle_vin.is_(None))).all() == []


# --- a row written before KAN-84 --------------------------------------------------------


def _unlabelled_party(db_session, vehicle_id: str, customer_id: str) -> uuid.UUID:
    party = VehicleParty(vehicle_id=uuid.UUID(vehicle_id), customer_id=uuid.UUID(customer_id), role=VehiclePartyRole.OWNER)
    db_session.add(party)
    db_session.commit()
    return party.id


def test_an_unlabelled_row_is_filled_for_a_read_without_writing(client, db_session):
    dealer_id, customer, vehicle = _setup(client)
    party_id = _unlabelled_party(db_session, vehicle["id"], customer["id"])

    listed = _list(client, dealer_id, customer["id"])

    assert listed[0]["vehicle"]["vin"] == vehicle["vin"]
    assert listed[0]["vehicle"]["vehicleNumber"] == vehicle["vehicleNumber"]
    row = _party(db_session, party_id)
    assert row.vehicle_vin is None and row.vehicle_label_refreshed_at is None  # a GET never writes


def test_the_next_write_to_an_unlabelled_row_stores_its_label(client, db_session):
    dealer_id, customer, vehicle = _setup(client)
    party_id = _unlabelled_party(db_session, vehicle["id"], customer["id"])
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    response = client.patch(
        f"/v1/customers/{customer['id']}/vehicles/{party_id}",
        json={"effectiveFrom": "2026-01-01T00:00:00Z"},
        headers=_bearer(token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["vehicle"]["vin"] == vehicle["vin"]
    row = _party(db_session, party_id)
    assert row.vehicle_vin == vehicle["vin"] and row.vehicle_label_refreshed_at is not None


def test_reconfirming_the_holder_of_an_unlabelled_row_labels_it(client, db_session):
    """Review finding 1: the ADR-064 re-confirm no-op returns the stored row
    as-is — without this it had no label and the response failed (500)."""

    dealer_id, customer, vehicle = _setup(client)
    party_id = _unlabelled_party(db_session, vehicle["id"], customer["id"])

    body = _link(client, dealer_id, customer["id"], vehicle["id"], role="owner")

    assert body["id"] == str(party_id)
    assert body["vehicle"]["vin"] == vehicle["vin"]
    assert _party(db_session, party_id).vehicle_vin == vehicle["vin"]


def test_disconnecting_an_unlabelled_row_stores_its_label(client, db_session):
    dealer_id, customer, vehicle = _setup(client)
    party_id = _unlabelled_party(db_session, vehicle["id"], customer["id"])
    token = _token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id))

    response = client.delete(f"/v1/customers/{customer['id']}/vehicles/{party_id}", headers=_bearer(token))

    assert response.status_code == 204, response.text
    row = _party(db_session, party_id)
    assert row.effective_to is not None
    assert row.vehicle_vin == vehicle["vin"] and row.vehicle_label_refreshed_at is not None


def test_a_read_fill_never_hides_the_empty_columns_from_a_later_write(client, db_session):
    """Review finding 3: a read and a write of the same row in ONE session.
    The read's fill must not make the write think the label is already
    stored (it used to: NULL labels stamped as fresh)."""

    from app.customer.services import customer as customer_service

    _dealer_id, customer, vehicle = _setup(client)
    party_id = _unlabelled_party(db_session, vehicle["id"], customer["id"])

    listed = customer_service.list_customer_vehicles(db_session, customer_id=uuid.UUID(customer["id"]))
    assert listed[0].vehicle.vin == vehicle["vin"]
    customer_service.get_customer_vehicle_or_404(
        db_session, customer_id=uuid.UUID(customer["id"]), party_id=party_id
    )
    db_session.commit()

    row = _party(db_session, party_id)
    assert row.vehicle_vin == vehicle["vin"] and row.vehicle_label_refreshed_at is not None
    assert row.vehicle.vin == vehicle["vin"]


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_none_until_a_row_is_labelled_then_the_stalest_age(client, db_session):
    from app.customer.services.customer import oldest_vehicle_party_label_age_seconds

    dealer_id, customer, vehicle = _setup(client)
    _unlabelled_party(db_session, vehicle["id"], customer["id"])
    assert oldest_vehicle_party_label_age_seconds(db_session) is None

    other = _create_vehicle(client, dealer_id)
    body = _link(client, dealer_id, customer["id"], other["id"], role="keeper")
    row = _party(db_session, body["id"])
    row.vehicle_label_refreshed_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=30)
    db_session.commit()

    age = oldest_vehicle_party_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600


def test_the_worker_heartbeat_records_the_label_age(client, db_session, monkeypatch):
    recorded = []
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.append((label, s)))
    dealer_id, customer, vehicle = _setup(client)
    _link(client, dealer_id, customer["id"], vehicle["id"])

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert len(recorded) == 1 and recorded[0][0] == "vehicle_party.vehicle" and recorded[0][1] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_label_refresh_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("integration.daily_jobs") < names.index("customer.vehicle_party_labels")
    assert names.index("customer.vehicle_party_labels") < names.index("reconciliation.run_all")

"""KAN-673 — a party write that names a merged vehicle-mdm record is carried
out on the record it was merged into.

«Allocate to customer» (`POST /v1/vehicle-mdm/{id}/allocate`) and the
customer-side link (`POST /v1/customers/{id}/vehicles`) used to write their
row on the duplicate once its `vehicle.vehicle_mdm.merged` had been consumed:
nothing moved it any more and only the nightly check «vehicle_party.vehicle_id
names a merged vehicle» said so. Both now follow the merge chain
(`vehicle.public.resolve_merged_vehicle_id`), take the (vehicle, role, group)
lock of the surviving record and answer with its id.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.reconciliation import find_rows_in_state
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.customer.reconciliation import CHECKS as CUSTOMER_CHECKS
from app.customer.services import vehicle_party_locks
from app.customer.services.vehicle_party import allocate_vehicle_party
from app.customer.services.vehicle_party_locks import _lock_vehicle_role_in_group
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import get_vehicle_mdm_or_404
from app.vehicle.services.merge import merge_vehicles
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_sales_lifecycle_reservation import _customer as _service_customer
from tests.test_vehicle_idempotency import _customer, _headers, _setup, _vehicle_mdm
from tests.test_vehicle_merge import _deliver_merged, _merged_message

OWNER = VehiclePartyRole.OWNER
_MERGED_CHECK = next(c for c in CUSTOMER_CHECKS if c.label == "vehicle_party.vehicle_id names a merged vehicle")


def _merge(db, engine, *, survivor: str, duplicate: str) -> None:
    """The merge and its consumed fact — the state the ticket names: the
    consumer has run, so nothing moves a row written on the duplicate later."""

    merge_vehicles(db, survivor_id=uuid.UUID(survivor), duplicate_id=uuid.UUID(duplicate), actor_id=uuid.uuid4())
    _deliver_merged(engine, _merged_message(db, uuid.UUID(duplicate)))
    db.expire_all()


def _rows(db, vehicle_id: str) -> list[VehicleParty]:
    db.expire_all()
    return list(
        db.scalars(
            select(VehicleParty).where(VehicleParty.vehicle_id == uuid.UUID(vehicle_id)).order_by(VehicleParty.id)
        )
    )


def _allocate(client, token: str, vehicle_id: str, customer_id: str, role: str = "owner"):
    return client.post(
        f"/v1/vehicle-mdm/{vehicle_id}/allocate", json={"customerId": customer_id, "role": role}, headers=_headers(token)
    )


def _link(client, token: str, customer_id: str, vehicle_id: str, **body):
    return client.post(
        f"/v1/customers/{customer_id}/vehicles",
        json={"vehicleId": vehicle_id, "role": "owner", **body},
        headers=_headers(token),
    )


def test_an_allocation_on_a_merged_vehicle_lands_on_its_survivor(client, db_session, engine):
    token = _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    incumbent, holder = _customer(client, token), _customer(client, token)
    assert _allocate(client, token, survivor["id"], incumbent["id"]).status_code == 201
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    response = _allocate(client, token, duplicate["id"], holder["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["vehicleId"] == survivor["id"]
    assert body["customerId"] == holder["id"]
    assert _rows(db_session, duplicate["id"]) == []
    on_survivor = {str(row.customer_id): row for row in _rows(db_session, survivor["id"])}
    assert on_survivor[holder["id"]].id == uuid.UUID(body["id"])
    assert on_survivor[holder["id"]].effective_to is None
    # The close rule ran on the survivor: its holder until now is closed.
    assert on_survivor[incumbent["id"]].effective_to is not None
    # The label is the survivor's (KAN-84), never the merged record's.
    assert on_survivor[holder["id"]].vehicle_vin == survivor["vin"]
    assert find_rows_in_state(db_session, _MERGED_CHECK) == []
    # The stale tab's next read still answers, under the id it asked for.
    holders = client.get(f"/v1/vehicle-mdm/{survivor['id']}/party-roles", headers=_headers(token))
    assert [row["customerId"] for row in holders.json()] == [holder["id"]]


def test_re_confirming_the_survivors_holder_through_the_merged_id_is_the_same_no_op(client, db_session, engine):
    token = _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    holder = _customer(client, token)
    first = _allocate(client, token, survivor["id"], holder["id"])
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    again = _allocate(client, token, duplicate["id"], holder["id"])

    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]
    assert len(_rows(db_session, survivor["id"])) == 1
    assert _rows(db_session, duplicate["id"]) == []


def test_a_customer_side_link_to_a_merged_vehicle_lands_on_its_survivor(client, db_session, engine):
    token = _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    holder = _customer(client, token)
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    response = _link(client, token, holder["id"], duplicate["id"])

    assert response.status_code == 201, response.text
    assert response.json()["vehicle"]["id"] == survivor["id"]
    assert response.json()["vehicle"]["vin"] == survivor["vin"]
    assert _rows(db_session, duplicate["id"]) == []
    [row] = _rows(db_session, survivor["id"])
    assert (str(row.customer_id), row.effective_to) == (holder["id"], None)
    assert find_rows_in_state(db_session, _MERGED_CHECK) == []


def test_a_history_row_created_on_a_merged_vehicle_lands_on_its_survivor(client, db_session, engine):
    """A create already ended is history and takes its own path past the
    allocation (KAN-148, KAN-370) — the second writer of a new party row."""

    token = _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    holder = _customer(client, token)
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    response = _link(
        client, token, holder["id"], duplicate["id"],
        effectiveFrom="2020-01-01T00:00:00Z", effectiveTo="2021-01-01T00:00:00Z",
    )

    assert response.status_code == 201, response.text
    assert response.json()["vehicle"]["id"] == survivor["id"]
    assert _rows(db_session, duplicate["id"]) == []
    [row] = _rows(db_session, survivor["id"])
    assert row.effective_to == dt.datetime(2021, 1, 1, tzinfo=dt.UTC)
    assert row.vehicle_vin == survivor["vin"]
    assert find_rows_in_state(db_session, _MERGED_CHECK) == []


def test_a_chain_of_two_merges_is_followed_to_the_live_record(client, db_session, engine):
    token = _setup(client)
    first, second, live = (_vehicle_mdm(client, token) for _ in range(3))
    holder, keeper = _customer(client, token), _customer(client, token)
    _merge(db_session, engine, survivor=second["id"], duplicate=first["id"])
    _merge(db_session, engine, survivor=live["id"], duplicate=second["id"])

    allocated = _allocate(client, token, first["id"], holder["id"])
    linked = _link(client, token, keeper["id"], first["id"], role="keeper")

    assert allocated.status_code == 201, allocated.text
    assert linked.status_code == 201, linked.text
    assert allocated.json()["vehicleId"] == live["id"]
    assert linked.json()["vehicle"]["id"] == live["id"]
    assert _rows(db_session, first["id"]) == []
    assert _rows(db_session, second["id"]) == []
    assert len(_rows(db_session, live["id"])) == 2
    assert find_rows_in_state(db_session, _MERGED_CHECK) == []


def test_an_unknown_vehicle_is_still_a_404_and_writes_nothing(client, db_session):
    token = _setup(client)
    holder = _customer(client, token)
    unknown = str(uuid.uuid4())

    allocated = _allocate(client, token, unknown, holder["id"])
    linked = _link(client, token, holder["id"], unknown)

    assert allocated.status_code == 404, allocated.text
    assert linked.status_code == 404, linked.text
    # Before anything touches customer: the vehicle's 404, not the customer's.
    missing_both = _allocate(client, token, unknown, str(uuid.uuid4()))
    assert missing_both.status_code == 404
    assert "Vehicle" in missing_both.json()["error"]["message"]
    assert _rows(db_session, unknown) == []


def test_another_groups_customer_on_a_merged_vehicle_is_still_a_404(client, db_session, engine):
    """Rule 7: following the merge changes which car, never whose customer."""

    token, elsewhere = _setup(client), _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    foreign = _customer(client, elsewhere)
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    allocated = _allocate(client, token, duplicate["id"], foreign["id"])
    linked = _link(client, token, foreign["id"], duplicate["id"])

    assert allocated.status_code == 404, allocated.text
    assert linked.status_code == 404, linked.text
    assert _rows(db_session, survivor["id"]) == []
    assert _rows(db_session, duplicate["id"]) == []


def test_each_dealer_group_keeps_its_own_holder_on_the_survivor(client, db_session, engine):
    """KAN-99: an allocation through the merged id closes only its own
    group's holder of the survivor."""

    token, elsewhere = _setup(client), _setup(client)
    survivor, duplicate = _vehicle_mdm(client, token), _vehicle_mdm(client, token)
    theirs, ours = _customer(client, elsewhere), _customer(client, token)
    assert _allocate(client, elsewhere, survivor["id"], theirs["id"]).status_code == 201
    _merge(db_session, engine, survivor=survivor["id"], duplicate=duplicate["id"])

    response = _allocate(client, token, duplicate["id"], ours["id"])

    assert response.status_code == 201, response.text
    assert {str(row.customer_id): row.effective_to for row in _rows(db_session, survivor["id"])} == {
        theirs["id"]: None,
        ours["id"]: None,
    }


def _two_cars(db):
    return (
        create_vehicle_mdm(db, vin="ZAR94000007123456", catalogue_variant_id=None),
        create_vehicle_mdm(db, vin="WVWZZZ1JZXW000001", catalogue_variant_id=None),
    )


@NEEDS_ITS_OWN_CONNECTION
@pytest.mark.parametrize("locked", ["survivor", "duplicate"])
def test_the_write_takes_the_survivors_role_lock_not_the_duplicates(db_session, engine, locked):
    survivor, duplicate = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _service_customer(db_session, group_id)
    merge_vehicles(db_session, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
    db_session.commit()
    held_vehicle = survivor.id if locked == "survivor" else duplicate.id
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def allocate(session) -> VehicleParty:
        session.execute(text("SET LOCAL lock_timeout = '300ms'"))
        return allocate_vehicle_party(
            session, vehicle_id=duplicate.id, customer_id=customer.id, role=OWNER, group_id=group_id,
            actor_id=uuid.uuid4(),
        )

    with factory() as holder, factory() as writer:
        _lock_vehicle_role_in_group(holder, vehicle_id=held_vehicle, role=OWNER, group_id=group_id)
        try:
            if locked == "survivor":
                with pytest.raises(OperationalError, match="lock timeout"):
                    allocate(writer)
                writer.rollback()
            else:
                assert allocate(writer).vehicle_id == survivor.id
        finally:
            holder.rollback()
            writer.rollback()


@NEEDS_ITS_OWN_CONNECTION
def test_a_merge_committed_after_the_request_read_its_vehicle_is_still_followed(db_session, engine):
    """The route reads the vehicle for its 404 before the write; a merge that
    commits after that read is seen by the write, which asks the database and
    not the session's copy of the row."""

    survivor, duplicate = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _service_customer(db_session, group_id)
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    with factory() as request, factory() as merge:
        assert get_vehicle_mdm_or_404(request, duplicate.id).merged_into_vehicle_id is None
        merge_vehicles(merge, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
        party = allocate_vehicle_party(
            request, vehicle_id=duplicate.id, customer_id=customer.id, role=OWNER, group_id=group_id,
            actor_id=uuid.uuid4(),
        )
        assert party.vehicle_id == survivor.id


@NEEDS_ITS_OWN_CONNECTION
def test_a_merge_committed_while_the_write_waited_for_its_lock_is_followed(db_session, engine, monkeypatch):
    """The window between resolving the record and holding its role lock: the
    record is read again under the lock, and a merge that committed meanwhile
    moves the write (and the lock) to the survivor."""

    survivor, duplicate = _two_cars(db_session)
    group_id = uuid.uuid4()
    customer = _service_customer(db_session, group_id)
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    take_lock = vehicle_party_locks._lock_vehicle_role_in_group
    locked: list[uuid.UUID] = []

    def lock_while_a_merge_commits(db, *, vehicle_id, role, group_id):
        if not locked:
            with factory() as merge:
                merge_vehicles(merge, survivor_id=survivor.id, duplicate_id=duplicate.id, actor_id=uuid.uuid4())
        locked.append(vehicle_id)
        take_lock(db, vehicle_id=vehicle_id, role=role, group_id=group_id)

    monkeypatch.setattr(vehicle_party_locks, "_lock_vehicle_role_in_group", lock_while_a_merge_commits)

    with factory() as request:
        party = allocate_vehicle_party(
            request, vehicle_id=duplicate.id, customer_id=customer.id, role=OWNER, group_id=group_id,
            actor_id=uuid.uuid4(),
        )
        assert party.vehicle_id == survivor.id
    assert locked == [duplicate.id, survivor.id]
    db_session.expire_all()
    assert db_session.get(VehicleMdm, duplicate.id).merged_into_vehicle_id == survivor.id
    assert find_rows_in_state(db_session, _MERGED_CHECK) == []

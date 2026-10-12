"""KAN-244 and KAN-257: how Stock turns a VIN into a vehicle-mdm record.

KAN-244 (Anto ticked A + C): a VIN sent to `POST /v1/inventory/stock-items`
is normalised (upper-cased, every whitespace character removed) and then
validated as vehicle-mdm validates it; one still malformed is a 422. A
create with a VIN finds or creates the vehicle-mdm record from the VIN, as
promotion does, and ignores a `vehicleId` the client sent. A create without
a VIN (pipeline-style) keeps what it did before.

KAN-257: two writers resolving a VIN vehicle-mdm has never seen both miss
the lookup and both insert; vehicle-mdm's own unique index refuses the
second. The loser resolves the VIN to the record the winner created
(FR-V-15: same VIN, same car) and carries on — it then promotes, or is
refused with `vin_already_in_stock` when the winner took the car into the
same dealership's stock.
"""

import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.stock_item import create_stock_item
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import create_or_get_vehicle_mdm
from app.vehicle.services import vehicle_mdm as vehicle_mdm_service

VIN = "WVWZZZ1KZAW000001"

_postgres_only = pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="the insert race needs a second connection that sees the first one's commit — Postgres lane",
)


def _bearer(tenant_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> dict[str, str]:
    tid = tenant_id or uuid.uuid4()
    token = create_access_token(
        user_id=user_id or uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


def _post(client, headers, **fields):
    body = {"vehicleLabel": "Volkswagen Golf", "condition": "used", **fields}
    return client.post("/v1/inventory/stock-items", json=body, headers=headers)


def _vehicle_by_vin(db_session, vin: str) -> VehicleMdm | None:
    return db_session.scalar(select(VehicleMdm).where(VehicleMdm.vin == vin))


def _stock_item_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(StockItem))


# --- KAN-244 A: the VIN is normalised, then validated ------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        VIN.lower(),
        "WVW ZZZ 1KZ AW0 00001",
        " wvwzzz1kzaw000001 ",
        "WVWZZZ1KZ\tAW000001\n",
        "wVw-ZZZ1KZAW000001".replace("-", " "),  # a no-break space pasted from a document
    ],
)
def test_a_differently_spelled_vin_of_a_car_in_stock_is_a_duplicate(client, spelling):
    headers = _bearer()
    first = _post(client, headers, vin=VIN)
    assert first.status_code == 201, first.text

    second = _post(client, headers, vin=spelling)

    assert second.status_code == 409, second.text
    assert second.json()["error"]["details"] == {
        "reason": "vin_already_in_stock",
        "stockItemId": first.json()["id"],
        "stockNumber": first.json()["stockNumber"],
    }


def test_the_vin_is_stored_as_vehicle_mdm_spells_it(client, db_session):
    created = _post(client, _bearer(), vin=" wvw zzz1kzaw000001 ")

    assert created.status_code == 201, created.text
    assert created.json()["vin"] == VIN
    assert created.json()["lifecycleStatus"] == "in_stock"
    vehicle = _vehicle_by_vin(db_session, VIN)
    assert vehicle is not None
    assert created.json()["vehicleId"] == str(vehicle.id)


@pytest.mark.parametrize(
    "malformed",
    [
        "WVWZZZ1KZAW00000",  # 16 characters
        "WVWZZZ1KZAW0000011",  # 18 characters
        "WVWZZZ1KZAI000001",  # I is never in a VIN
        "WVWZZZ1KZAO000001",  # nor O
        "WVWZZZ1KZAQ000001",  # nor Q
        "WVWZZZ1KZAW00000-",
        "",
        "   ",
    ],
)
def test_a_vin_still_malformed_after_normalising_is_422_and_writes_nothing(client, db_session, malformed):
    refused = _post(client, _bearer(), vin=malformed)

    assert refused.status_code == 422, refused.text
    errors = refused.json()["error"]["details"]["errors"]
    assert [e["loc"] for e in errors] == [["body", "vin"]]
    assert _stock_item_count(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 0


# --- KAN-244 C: the vehicle is resolved from the VIN --------------------------


def test_a_create_with_a_vin_ignores_the_clients_vehicle_id_and_creates_the_vehicle(client, db_session):
    someone_elses = uuid.uuid4()

    created = _post(client, _bearer(), vin=VIN, vehicleId=str(someone_elses))

    assert created.status_code == 201, created.text
    vehicle = _vehicle_by_vin(db_session, VIN)
    assert vehicle is not None
    assert created.json()["vehicleId"] == str(vehicle.id)
    assert created.json()["vehicleId"] != str(someone_elses)


def test_the_vehicle_a_create_resolves_names_the_user_who_created_it(client, db_session):
    """KAN-243: a null created_by means a system write; a user's stock create is not one."""
    user_id = uuid.uuid4()

    created = _post(client, _bearer(user_id=user_id), vin=VIN)

    assert created.status_code == 201, created.text
    assert _vehicle_by_vin(db_session, VIN).created_by == user_id


def test_a_create_with_a_vin_vehicle_mdm_knows_uses_that_record(client, db_session):
    known, _created = create_or_get_vehicle_mdm(db_session, vin=VIN)
    other, _created = create_or_get_vehicle_mdm(db_session, vin="ZFA31200000123456")

    created = _post(client, _bearer(), vin=VIN.lower(), vehicleId=str(other.id))

    assert created.status_code == 201, created.text
    assert created.json()["vehicleId"] == str(known.id)
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 2


def test_a_create_without_a_vin_keeps_todays_behaviour_for_vehicle_id(client):
    """Unchanged by KAN-244 (Anto: "keep today's behaviour"): a pipeline-style
    create stores the vehicleId it was sent, unverified, and stays pipeline."""

    sent = uuid.uuid4()

    created = _post(client, _bearer(), vehicleId=str(sent))

    assert created.status_code == 201, created.text
    assert created.json()["vehicleId"] == str(sent)
    assert created.json()["vin"] is None
    assert created.json()["lifecycleStatus"] == "pipeline"


def test_the_openapi_says_vehicle_id_is_ignored_when_a_vin_is_sent(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]["StockItemCreate"]["properties"]

    assert "Ignored when `vin` is sent" in schema["vehicleId"]["description"]
    assert "normalised" in schema["vin"]["description"].lower()


def test_the_schema_docstring_no_longer_claims_vin_is_never_writable():
    import app.inventory.schemas.stock_item as schemas

    assert "never writable" not in (schemas.__doc__ or "")


# --- KAN-257: losing vehicle-mdm's insert race -------------------------------


def _lose_the_first_lookup(monkeypatch, engine, *, winner) -> list[str]:
    """The race, made deterministic: the first VIN lookup misses while
    another writer (`winner`, on a connection of its own) creates — and
    commits — the same VIN first. Every later lookup is the real one."""

    real_lookup = vehicle_mdm_service.get_vehicle_mdm_by_vin
    misses: list[str] = []

    def lookup(db, vin):
        if not misses:
            misses.append(vin)
            with Session(bind=engine, autoflush=False, expire_on_commit=False) as other:
                winner(other, vin)
            return None
        return real_lookup(db, vin)

    monkeypatch.setattr(vehicle_mdm_service, "get_vehicle_mdm_by_vin", lookup)
    return misses


def _pipeline(db_session, tenant_id: uuid.UUID) -> StockItem:
    return create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Volkswagen Golf (Eintausch)", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )


@_postgres_only
def test_a_promotion_that_loses_the_insert_race_promotes_onto_the_winners_record(db_session, engine, monkeypatch):
    """The winner is another dealership taking the same car (a VIN is
    global, stock is per dealership): this dealership's promotion goes on."""

    tenant_id = uuid.uuid4()
    trade_in = _pipeline(db_session, tenant_id)
    winners: list[uuid.UUID] = []

    def another_dealership_promotes(other, vin):
        winners.append(vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None).id)

    misses = _lose_the_first_lookup(monkeypatch, engine, winner=another_dealership_promotes)

    promoted = promote_to_vehicle_mdm(db_session, item=trade_in, vin=VIN)

    assert misses == [VIN]  # the race really happened
    assert promoted.lifecycle_status == LifecycleStatus.IN_STOCK
    assert promoted.vin == VIN
    assert promoted.vehicle_id == winners[0]
    assert db_session.scalar(select(func.count()).select_from(VehicleMdm)) == 1


@_postgres_only
def test_a_promotion_that_loses_the_race_to_its_own_dealership_is_refused_vin_already_in_stock(
    db_session, engine, monkeypatch
):
    """The winner took the car into this dealership's stock: the loser
    resolves the VIN, then the in-stock rule refuses it with its reason."""

    tenant_id = uuid.uuid4()
    trade_in = _pipeline(db_session, tenant_id)
    held: list[StockItem] = []

    def the_same_dealership_takes_the_car_in(other, vin):
        # A promotion's order: vehicle-mdm first (its own commit), then the item.
        vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None)
        held.append(
            create_stock_item(
                other, tenant_id=tenant_id,
                data=StockItemCreate(vehicle_label="Volkswagen Golf", condition=StockItemCondition.USED, vin=vin),
                actor_id=None,
            )
        )

    _lose_the_first_lookup(monkeypatch, engine, winner=the_same_dealership_takes_the_car_in)

    with pytest.raises(ConflictError) as refused:
        promote_to_vehicle_mdm(db_session, item=trade_in, vin=VIN)

    assert refused.value.details == {
        "reason": "vin_already_in_stock",
        "stockItemId": str(held[0].id),
        "stockNumber": held[0].stock_number,
    }
    db_session.rollback()
    db_session.refresh(trade_in)
    assert trade_in.lifecycle_status == LifecycleStatus.PIPELINE
    assert trade_in.vehicle_id is None


@_postgres_only
def test_losing_the_race_never_discards_the_callers_own_pending_work(db_session, engine, monkeypatch):
    """vehicle-mdm's guard rolls back the session it is handed. Stock hands
    it a session of its own, so the caller's pending rows survive the lost
    race, and are not committed by vehicle-mdm's commit either."""

    tenant_id = uuid.uuid4()
    trade_in = _pipeline(db_session, tenant_id)
    callers_work = stock_item_service._build_and_flush_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="The caller's own pending item", condition=StockItemCondition.NEW),
        actor_id=None,
        pipeline_ref=None,
    )

    def another_dealership_promotes(other, vin):
        vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None)

    _lose_the_first_lookup(monkeypatch, engine, winner=another_dealership_promotes)

    promote_to_vehicle_mdm(db_session, item=trade_in, vin=VIN)

    # promote_to_vehicle_mdm commits its caller's transaction at its end, as
    # it always has; the pending row is in it, not lost.
    assert db_session.scalar(select(StockItem.id).where(StockItem.id == callers_work.id)) == callers_work.id


@_postgres_only
def test_a_create_that_loses_the_insert_race_uses_the_winners_record(client, db_session, engine, monkeypatch):
    winners: list[uuid.UUID] = []

    def another_dealership_takes_the_car(other, vin):
        winners.append(vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None).id)

    misses = _lose_the_first_lookup(monkeypatch, engine, winner=another_dealership_takes_the_car)

    created = _post(client, _bearer(), vin=VIN, vehicleId=str(uuid.uuid4()))

    assert misses == [VIN]
    assert created.status_code == 201, created.text
    assert created.json()["vehicleId"] == str(winners[0])


@_postgres_only
def test_a_create_that_loses_the_race_to_its_own_dealership_is_409_vin_already_in_stock(
    client, db_session, engine, monkeypatch
):
    tenant_id = uuid.uuid4()
    held: list[StockItem] = []

    def the_same_dealership_takes_the_car_in(other, vin):
        # A promotion's order: vehicle-mdm first (its own commit), then the item.
        vehicle_mdm_service.create_vehicle_mdm(other, vin=vin, catalogue_variant_id=None)
        held.append(
            create_stock_item(
                other, tenant_id=tenant_id,
                data=StockItemCreate(vehicle_label="Volkswagen Golf", condition=StockItemCondition.USED, vin=vin),
                actor_id=None,
            )
        )

    _lose_the_first_lookup(monkeypatch, engine, winner=the_same_dealership_takes_the_car_in)

    refused = _post(client, _bearer(tenant_id), vin=VIN)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == {
        "reason": "vin_already_in_stock",
        "stockItemId": str(held[0].id),
        "stockNumber": held[0].stock_number,
    }

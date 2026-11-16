"""KAN-739: a stock item's location carries its name beside its id (rule 2's
three-column pattern), so the Stock grid can show «Standort» (ADR-060; Anto,
2026-11-15: visible by default).

- Set on create and on every PATCH that moves the id, read through
  `app.platform.public.get_location_labels`; another Betrieb's or an unknown
  location is 422 `unknown_location`, its name never stored.
- The nightly `inventory.location_labels` follows a renamed location (no
  version bump), restamps a current one, leaves another Betrieb's alone, and
  reports its age.
- `scripts/backfill_stock_item_location_labels.py` fills the rows written
  before: dry run writes nothing, --commit fills, a second run changes nothing.
On Postgres.
"""

import datetime as dt
import uuid

import pytest

from app.core.auth import AccessRole
from app.core.errors import UnprocessableEntityError
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.public import oldest_stock_location_label_age_seconds, refresh_stock_location_labels
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.stock_item import create_stock_item
from app.platform.organisation.models.dealership import Location
from app.worker import register_daily_jobs
from scripts.backfill_stock_item_location_labels import backfill
from tests.test_grid_exclusion_filters import _headers
from tests.test_integration_connection_location import _dealership, _location


def _item(db_session, tenant_id: uuid.UUID, location_id: uuid.UUID | None = None) -> StockItem:
    return create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED, location_id=location_id),
        actor_id=None,
    )


def _patch(client, item: StockItem, body: dict):
    return client.patch(
        f"/v1/inventory/stock-items/{item.id}",
        json=body,
        headers={**_headers(item.tenant_id, AccessRole.INVENTORY), "If-Match": str(item.version)},
    )


# --- the write path ---------------------------------------------------------------------


def test_a_create_stores_the_location_s_name_beside_its_id(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")

    item = _item(db_session, dealership.id, location.id)

    assert (item.location_id, item.location_label) == (location.id, "Zürich Nord")
    assert item.location_label_refreshed_at is not None
    response = client.get(f"/v1/inventory/stock-items/{item.id}", headers=_headers(dealership.id, AccessRole.INVENTORY))
    assert response.status_code == 200, response.text
    assert response.json()["locationLabel"] == "Zürich Nord"


def test_another_betriebs_location_is_refused_and_its_name_never_stored(db_session):
    dealership = _dealership(db_session)
    foreign = _location(db_session, _dealership(db_session), "Bern")

    for location_id in (foreign.id, uuid.uuid4()):
        with pytest.raises(UnprocessableEntityError) as refused:
            _item(db_session, dealership.id, location_id)
        assert refused.value.details["reason"] == "unknown_location"
        db_session.rollback()
    assert db_session.query(StockItem).filter(StockItem.location_label == "Bern").count() == 0


def test_a_patch_moving_the_location_moves_its_name_and_null_clears_both(client, db_session):
    dealership = _dealership(db_session)
    north = _location(db_session, dealership, "Zürich Nord")
    south = _location(db_session, dealership, "Zürich Süd")
    item = _item(db_session, dealership.id, north.id)

    moved = _patch(client, item, {"locationId": str(south.id)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["locationLabel"] == "Zürich Süd"

    db_session.expire_all()
    item = db_session.get(StockItem, item.id)
    cleared = _patch(client, item, {"locationId": None})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["locationId"], cleared.json()["locationLabel"]) == (None, None)
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).location_label_refreshed_at is None


def test_a_patch_naming_another_betriebs_location_is_422(client, db_session):
    dealership = _dealership(db_session)
    item = _item(db_session, dealership.id)
    foreign = _location(db_session, _dealership(db_session), "Bern")

    response = _patch(client, item, {"locationId": str(foreign.id)})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "unknown_location"
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).location_id is None


def test_a_patch_leaving_the_location_out_keeps_it(client, db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    item = _item(db_session, dealership.id, location.id)

    response = _patch(client, item, {"odometerKm": 12000})

    assert response.status_code == 200, response.text
    assert (response.json()["locationId"], response.json()["locationLabel"]) == (str(location.id), "Zürich Nord")


# --- the nightly refresh ----------------------------------------------------------------


def test_the_label_follows_a_renamed_location_without_a_version_bump(db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    item = _item(db_session, dealership.id, location.id)
    version_before = item.version
    db_session.get(Location, location.id).name = "Zürich Oerlikon"
    db_session.commit()

    report = refresh_stock_location_labels(db_session)

    assert report.changed == [(item.id, "Zürich Nord", "Zürich Oerlikon")]
    db_session.expire_all()
    row = db_session.get(StockItem, item.id)
    assert (row.location_label, row.version) == ("Zürich Oerlikon", version_before)
    again = refresh_stock_location_labels(db_session)
    assert (again.changed, again.restamped) == ([], 1)
    age = oldest_stock_location_label_age_seconds(db_session)
    assert age is not None and age < 60


def test_the_refresh_leaves_another_betriebs_location_alone(db_session):
    dealership = _dealership(db_session)
    foreign = _location(db_session, _dealership(db_session), "Bern")
    item = _item(db_session, dealership.id)
    stale = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    item.location_id, item.location_label, item.location_label_refreshed_at = foreign.id, "Old", stale
    db_session.commit()

    report = refresh_stock_location_labels(db_session)

    assert (report.changed, report.unresolved) == ([], [(item.id, foreign.id)])
    db_session.expire_all()
    row = db_session.get(StockItem, item.id)
    assert (row.location_label, row.location_label_refreshed_at) == ("Old", stale)


def test_the_worker_registers_the_refresh(monkeypatch):
    registered: list[str] = []
    monkeypatch.setattr("app.worker.register_daily_job", lambda name, _job: registered.append(name))

    register_daily_jobs()

    assert "inventory.location_labels" in registered
    assert registered.index("inventory.location_labels") < registered.index("reconciliation.run_all")


# --- the backfill -----------------------------------------------------------------------


def _written_before_kan739(db_session, tenant_id: uuid.UUID, location_id: uuid.UUID) -> StockItem:
    item = _item(db_session, tenant_id)
    item.location_id = location_id  # as a row written before the label existed
    db_session.commit()
    return item


def test_the_backfill_dry_run_writes_nothing_and_the_real_run_fills(db_session):
    dealership = _dealership(db_session)
    location = _location(db_session, dealership, "Zürich Nord")
    item = _written_before_kan739(db_session, dealership.id, location.id)

    dry = backfill(db_session, commit=False)
    assert dry.changed == [(item.id, None, "Zürich Nord")]
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).location_label is None

    real = backfill(db_session, commit=True)
    assert real.changed == [(item.id, None, "Zürich Nord")]
    db_session.expire_all()
    row = db_session.get(StockItem, item.id)
    assert (row.location_label, row.location_label_refreshed_at is not None) == ("Zürich Nord", True)

    assert backfill(db_session, commit=True).changed == []

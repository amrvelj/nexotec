"""KAN-534 — Stock's copy of a configuration's label
(`stock_item.configuration_label`, rule 2's three-column pattern) follows an
edit of the configuration within a day: the nightly
`inventory.configuration_labels` job re-reads every item's configuration
label through `vehicle.public.get_configuration_labels`, with the rules of
the other label jobs (KAN-84, KAN-403, KAN-404) — `version` not bumped, a
current label only restamped, a held item skipped, a configuration of another
Betrieb left alone — and its age is the gauge
`dms.label.age_seconds{label="inventory.configuration"}`. On Postgres.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.inventory.models.stock_item import StockItem
from app.inventory.public import oldest_configuration_label_age_seconds, refresh_configuration_labels
from app.vehicle.public import get_configuration_labels
from app.vehicle.services import configuration as configuration_service
from tests.test_customer_vehicle_party_close_rule import _postgres_only
from tests.test_inventory_purchase_configuration_vin import _configuration, _pipeline_item, _tenant_of
from tests.test_inventory_purchase_promotes import _token


def _item(db, item: dict) -> StockItem:
    db.expire_all()
    return db.get(StockItem, uuid.UUID(item["id"]))


def _edit_variant(db, token: str, configuration_id: str, variant: str) -> None:
    configuration = configuration_service.get_configuration_or_404(
        db, tenant_id=_tenant_of(token), configuration_id=uuid.UUID(configuration_id)
    )
    configuration.brand_display_name = "Volkswagen"
    configuration.model_group_name = "Golf"
    configuration.variant_name = variant
    db.commit()


def _backdate(db, item: dict, hours: int) -> dt.datetime:
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    row = _item(db, item)
    row.configuration_label_refreshed_at = then
    row.updated_at = then
    db.commit()
    return then


def _stocked(client, db_session) -> tuple[str, str, dict]:
    token = _token(client)
    configuration_id = _configuration(db_session, token, vin=None)
    return token, configuration_id, _pipeline_item(client, token, configuration_id)


def test_an_edited_configuration_renames_the_stock_item_after_the_nightly_job(client, db_session):
    token, configuration_id, item = _stocked(client, db_session)
    before = _item(db_session, item)
    version = before.version
    then = _backdate(db_session, item, hours=30)

    _edit_variant(db_session, token, configuration_id, "2.0 TDI Style")
    assert _item(db_session, item).configuration_label != "Volkswagen Golf 2.0 TDI Style"  # a copy, until the job runs

    assert refresh_configuration_labels(db_session) == 1

    after = _item(db_session, item)
    assert after.configuration_label == "Volkswagen Golf 2.0 TDI Style"
    assert after.configuration_label_refreshed_at > then
    assert after.updated_at > then  # what the API returns changed
    assert after.version == version  # a copy no client writes: no stale-version 409 on the open detail


def test_a_current_label_is_only_restamped(client, db_session):
    token, configuration_id, item = _stocked(client, db_session)
    _edit_variant(db_session, token, configuration_id, "1.5 eTSI Life")
    refresh_configuration_labels(db_session)
    then = _backdate(db_session, item, hours=30)

    assert refresh_configuration_labels(db_session) == 0

    row = _item(db_session, item)
    assert row.configuration_label == "Volkswagen Golf 1.5 eTSI Life"
    assert row.configuration_label_refreshed_at > then
    assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1  # updated_at held


def test_a_configuration_of_another_betrieb_is_left_alone(client, db_session):
    token, configuration_id, item = _stocked(client, db_session)
    _edit_variant(db_session, token, configuration_id, "R")
    then = _backdate(db_session, item, hours=30)
    row = _item(db_session, item)
    label = row.configuration_label
    row.tenant_id = uuid.uuid4()  # a reference reconciliation reports as a mismatch (rule 2)
    db_session.commit()

    assert refresh_configuration_labels(db_session) == 0

    row = _item(db_session, item)
    assert row.configuration_label == label
    assert abs((row.configuration_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_job_walks_more_than_one_batch(client, db_session, monkeypatch):
    from app.inventory.services import configuration_label

    monkeypatch.setattr(configuration_label, "_BATCH", 2)
    token = _token(client)
    stocked = []
    for _ in range(3):
        configuration_id = _configuration(db_session, token, vin=None)
        stocked.append((configuration_id, _pipeline_item(client, token, configuration_id)))
    for configuration_id, _item_body in stocked:
        _edit_variant(db_session, token, configuration_id, "GTI")

    assert refresh_configuration_labels(db_session) == 3
    assert {_item(db_session, body).configuration_label for _cid, body in stocked} == {"Volkswagen Golf GTI"}


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_none_without_a_configuration_then_the_stalest(client, db_session):
    assert oldest_configuration_label_age_seconds(db_session) is None

    *_, item = _stocked(client, db_session)
    _backdate(db_session, item, hours=30)

    age = oldest_configuration_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600


def test_the_worker_heartbeat_records_the_configuration_label_age(client, db_session, monkeypatch):
    recorded = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.__setitem__(label, s))
    _stocked(client, db_session)

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["inventory.configuration"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_refresh_with_the_label_jobs_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("sales.owner_labels") < names.index("inventory.configuration_labels")
    assert names.index("inventory.configuration_labels") < names.index("reconciliation.run_all")


# --- locking: the job waits for nobody --------------------------------------------------


def test_an_item_another_writer_holds_is_skipped_until_the_next_night(engine, client, db_session):
    _postgres_only(engine)
    token = _token(client)
    held_configuration = _configuration(db_session, token, vin=None)
    held = _pipeline_item(client, token, held_configuration)
    free_configuration = _configuration(db_session, token, vin=None)
    free = _pipeline_item(client, token, free_configuration)
    then = _backdate(db_session, held, hours=30)
    _edit_variant(db_session, token, held_configuration, "Variant")
    _edit_variant(db_session, token, free_configuration, "Variant")

    holder = sessionmaker(bind=engine)()
    try:
        holder.execute(text("SELECT id FROM stock_item WHERE id = :id FOR NO KEY UPDATE"), {"id": uuid.UUID(held["id"])})
        job_db = sessionmaker(bind=engine)()
        try:
            job_db.execute(text("SET lock_timeout = '3s'"))  # a job that waited would fail here, not hang
            refresh_configuration_labels(job_db)
        finally:
            job_db.close()
    finally:
        holder.rollback()
        holder.close()

    assert _item(db_session, free).configuration_label == "Volkswagen Golf Variant"
    row = _item(db_session, held)
    assert row.configuration_label != "Volkswagen Golf Variant"
    assert abs((row.configuration_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1

    refresh_configuration_labels(db_session)  # the next night
    assert _item(db_session, held).configuration_label == "Volkswagen Golf Variant"


def test_the_vehicle_read_answers_only_in_the_betrieb_the_host_row_names(client, db_session):
    token, configuration_id, _item_body = _stocked(client, db_session)
    tenant = _tenant_of(token)
    config = uuid.UUID(configuration_id)

    assert set(get_configuration_labels(db_session, {config: tenant})) == {config}
    assert get_configuration_labels(db_session, {config: uuid.uuid4()}) == {}  # another Betrieb's: absent
    assert get_configuration_labels(db_session, {}) == {}

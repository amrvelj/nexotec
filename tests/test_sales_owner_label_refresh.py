"""KAN-404 — the responsible seller's label (`owner_label`, KAN-200) on
offers, contracts and deal rows follows a rename within a day: the nightly
`sales.owner_labels` job re-reads every seller's name through
`platform.public.get_user_display_labels`, with the customer advisor job's
rules (KAN-403) — a deactivated user keeps the label they had, an id naming
no user is left alone, `version` is not bumped, a held row is skipped.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.platform.models.user import User, UserStatus
from app.sales.models.contract import SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.public import oldest_owner_label_age_seconds, refresh_owner_labels
from tests.identity_support import create_dealership, create_user, platform_admin_headers
from tests.test_customer_vehicle_party_close_rule import _postgres_only
from tests.test_user_deactivation import _as, _contract_for, _customer_for, _offer_for


def _rename(client, dealership: str, user: dict, body: dict) -> None:
    current = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()
    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json=body,
        headers={**platform_admin_headers(), "If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text


def _rows(db, owner_id: str) -> list[SalesOffer | SalesContract | SalesDeal]:
    db.expire_all()
    owner = uuid.UUID(owner_id)
    return [
        row
        for model in (SalesOffer, SalesContract, SalesDeal)
        for row in db.scalars(select(model).where(model.owner_user_id == owner)).all()
    ]


def _backdate(db, rows, hours: int) -> dt.datetime:
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    for row in rows:
        row.owner_label_refreshed_at = then
        row.updated_at = then
    db.commit()
    return then


def _seller_with_deals(client, dealership: str, **names) -> tuple[dict, dict, dict]:
    seller = create_user(client, dealership, isDealerManager=False, **names)
    offer = _offer_for(client, seller)
    contract = _contract_for(client, seller, _customer_for(client, seller))
    return seller, offer, contract


def test_a_renamed_seller_reaches_offers_contracts_and_deal_rows_after_the_nightly_job(client, db_session):
    dealership = create_dealership(client)
    seller, offer, contract = _seller_with_deals(client, dealership, firstName="Lena", lastName="Meier")
    rows = _rows(db_session, seller["id"])
    assert {type(row) for row in rows} == {SalesOffer, SalesContract, SalesDeal}
    assert {row.owner_label for row in rows} == {"Lena Meier"}
    versions = {row.id: row.version for row in rows if not isinstance(row, SalesDeal)}
    then = _backdate(db_session, rows, hours=30)

    _rename(client, dealership, seller, {"lastName": "Keller"})
    assert {row.owner_label for row in _rows(db_session, seller["id"])} == {"Lena Meier"}  # a copy, until the job runs

    assert refresh_owner_labels(db_session) == len(rows)

    for row in _rows(db_session, seller["id"]):
        assert row.owner_label == "Lena Keller"
        assert row.owner_label_refreshed_at > then
        assert row.updated_at > then
        if not isinstance(row, SalesDeal):
            assert row.version == versions[row.id]  # a copy no client writes: no stale-version 409 for the seller

    headers = _as(seller)
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=headers).json()["ownerLabel"] == "Lena Keller"
    assert client.get(f"/v1/sales/contracts/{contract['id']}", headers=headers).json()["ownerLabel"] == "Lena Keller"
    deals = client.get(f"/v1/sales/deals?ownerUserId={seller['id']}", headers=headers).json()["items"]
    assert deals and {deal["ownerLabel"] for deal in deals} == {"Lena Keller"}


def test_a_current_label_is_only_restamped(client, db_session):
    dealership = create_dealership(client)
    seller, _offer, _contract = _seller_with_deals(client, dealership, firstName="Rey", lastName="Ortiz")
    then = _backdate(db_session, _rows(db_session, seller["id"]), hours=30)

    assert refresh_owner_labels(db_session) == 0

    for row in _rows(db_session, seller["id"]):
        assert row.owner_label == "Rey Ortiz"
        assert row.owner_label_refreshed_at > then
        assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_a_deactivated_seller_keeps_the_label_they_had(client, db_session):
    dealership = create_dealership(client)
    seller, _offer, _contract = _seller_with_deals(client, dealership, firstName="Mia", lastName="Frei")
    then = _backdate(db_session, _rows(db_session, seller["id"]), hours=30)

    _rename(client, dealership, seller, {"lastName": "Graf"})
    # The deactivation step hands open deals over; a deactivated seller still
    # names a closed deal, or an open one whose hand-over a module refused.
    # The status is set on the row, as those paths leave it.
    db_session.get(User, uuid.UUID(seller["id"])).status = UserStatus.DEACTIVATED
    db_session.commit()

    assert refresh_owner_labels(db_session) == 0

    for row in _rows(db_session, seller["id"]):
        assert row.owner_label == "Mia Frei"
        assert row.owner_label_refreshed_at > then


def test_an_owner_id_naming_no_user_is_left_alone(client, db_session):
    dealership = create_dealership(client)
    gone = create_user(client, dealership, isDealerManager=False, firstName="Gone", lastName="User")
    dangling = _offer_for(client, gone)
    renamed = create_user(client, dealership, isDealerManager=False, firstName="Noa", lastName="Huber")
    kept = _offer_for(client, renamed)

    nobody = uuid.uuid4()
    row = db_session.get(SalesOffer, uuid.UUID(dangling["id"]))
    row.owner_user_id = nobody
    then = _backdate(db_session, [row], hours=30)
    _rename(client, dealership, renamed, {"firstName": "Noah"})

    changed = refresh_owner_labels(db_session)

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(kept["id"])).owner_label == "Noah Huber"
    assert changed >= 1
    row = db_session.get(SalesOffer, uuid.UUID(dangling["id"]))
    assert row.owner_label == "Gone User"
    assert abs((row.owner_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_job_walks_more_than_one_batch(client, db_session, monkeypatch):
    from app.sales.services import owner_label

    monkeypatch.setattr(owner_label, "_BATCH", 2)
    dealership = create_dealership(client)
    seller = create_user(client, dealership, isDealerManager=False, firstName="Ben", lastName="Roth")
    offers = [_offer_for(client, seller) for _ in range(5)]
    _rename(client, dealership, seller, {"firstName": "Benjamin"})

    refresh_owner_labels(db_session)

    db_session.expire_all()
    assert {db_session.get(SalesOffer, uuid.UUID(o["id"])).owner_label for o in offers} == {"Benjamin Roth"}


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_none_without_a_seller_then_the_stalest_age_across_the_three_tables(client, db_session):
    assert oldest_owner_label_age_seconds(db_session) is None

    dealership = create_dealership(client)
    _seller, _offer, contract = _seller_with_deals(client, dealership)
    _backdate(db_session, [db_session.get(SalesContract, uuid.UUID(contract["id"]))], hours=30)

    age = oldest_owner_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600


def test_the_worker_heartbeat_records_the_owner_label_age(client, db_session, monkeypatch):
    recorded = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.__setitem__(label, s))
    dealership = create_dealership(client)
    _offer_for(client, create_user(client, dealership, isDealerManager=False))

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["sales.owner"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_owner_label_refresh_after_the_advisor_labels_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("customer.advisor_labels") < names.index("sales.owner_labels")
    assert names.index("sales.owner_labels") < names.index("reconciliation.run_all")


# --- locking: the job waits for nobody --------------------------------------------------


def test_an_offer_another_writer_holds_is_skipped_until_the_next_night(engine, client, db_session):
    _postgres_only(engine)
    dealership = create_dealership(client)
    seller = create_user(client, dealership, isDealerManager=False, firstName="Eva", lastName="Lang")
    held_offer = _offer_for(client, seller)
    free_offer = _offer_for(client, seller)
    then = _backdate(db_session, [db_session.get(SalesOffer, uuid.UUID(held_offer["id"]))], hours=30)
    _rename(client, dealership, seller, {"lastName": "Berg"})

    holder = sessionmaker(bind=engine)()
    try:
        holder.execute(
            text("SELECT id FROM sales_offer WHERE id = :id FOR NO KEY UPDATE"), {"id": uuid.UUID(held_offer["id"])}
        )
        job_db = sessionmaker(bind=engine)()
        try:
            # A job that waited would fail here instead of hanging the test.
            job_db.execute(text("SET lock_timeout = '3s'"))
            refresh_owner_labels(job_db)
        finally:
            job_db.close()
    finally:
        holder.rollback()
        holder.close()

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(free_offer["id"])).owner_label == "Eva Berg"
    held = db_session.get(SalesOffer, uuid.UUID(held_offer["id"]))
    assert held.owner_label == "Eva Lang"
    assert abs((held.owner_label_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1

    refresh_owner_labels(db_session)  # the next night
    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(held_offer["id"])).owner_label == "Eva Berg"

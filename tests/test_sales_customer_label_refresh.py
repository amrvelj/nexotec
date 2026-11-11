"""KAN-590 — a customer renamed after being attached to Sales' open work is
followed within a day.

Before this ticket only a merge relabelled Sales' rows (KAN-580): a customer
renamed later kept the old name on the Sales list, the offer and contract
headers and the next generated document's addressee. The nightly
`sales.customer_labels` job re-reads the label (and the correspondence
language) through `customer.public.get_customer_sales_labels`, with the
`sales.owner_labels` rules (KAN-404): `version` not bumped, a held row
skipped, an id naming no customer left alone.

Anto's ruling (2026-11-10, 1A): open offers, pending contracts and their deal
rows follow; a confirmed, invoiced or cancelled contract — and its deal row —
keeps the name the buyer signed under. `customer_locality` is left as it is.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.customer.models.customer import Customer, CustomerLifecycleStatus, Language
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.public import oldest_customer_label_age_seconds, refresh_customer_labels
from tests.identity_support import create_dealership, create_user
from tests.test_customer_vehicle_party_close_rule import _postgres_only
from tests.test_user_deactivation import _as, _contract_for, _customer_for, _offer_for


def _offer_with(client, seller: dict, customer: dict) -> dict:
    offer = _offer_for(client, seller)
    response = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"customerId": customer["id"]},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(db, customer_id: str) -> list[SalesOffer | SalesContract | SalesDeal]:
    db.expire_all()
    cid = uuid.UUID(customer_id)
    return [
        row
        for model in (SalesOffer, SalesContract, SalesDeal)
        for row in db.scalars(select(model).where(model.customer_id == cid)).all()
    ]


def _backdate(db, rows, hours: int) -> dt.datetime:
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    for row in rows:
        row.customer_denorm_refreshed_at = then
        row.updated_at = then
    db.commit()
    return then


def _rename(db, customer_id: str, *, last_name: str, language: Language | None = None) -> None:
    """The customer context's own write, as its PATCH leaves the row."""

    customer = db.get(Customer, uuid.UUID(customer_id))
    customer.last_name = last_name
    if language is not None:
        customer.language = language
    db.commit()


def _sign(db, contract_id: str, status: ContractStatus) -> None:
    """A contract past pending, as confirmation, invoicing or cancellation
    leaves it and its deal row."""

    contract = db.get(SalesContract, uuid.UUID(contract_id))
    contract.status = status
    for deal in db.scalars(select(SalesDeal).where(SalesDeal.contract_id == contract.id)).all():
        deal.status = status.value
    db.commit()


def _setup(client, **names):
    dealership = create_dealership(client)
    seller = create_user(client, dealership, isDealerManager=False)
    customer = _customer_for(client, seller, **names)
    return seller, customer


def test_a_rename_reaches_open_offers_pending_contracts_and_their_deal_rows(client, db_session):
    seller, customer = _setup(client, firstName="Hans", lastName="Müler")
    offer = _offer_with(client, seller, customer)
    contract = _contract_for(client, seller, customer)
    rows = _rows(db_session, customer["id"])
    assert {type(row) for row in rows} == {SalesOffer, SalesContract, SalesDeal}
    assert {row.customer_label for row in rows} == {"Hans Müler"}
    versions = {row.id: row.version for row in rows if not isinstance(row, SalesDeal)}
    then = _backdate(db_session, rows, hours=30)

    _rename(db_session, customer["id"], last_name="Müller", language=Language.FR)
    assert {row.customer_label for row in _rows(db_session, customer["id"])} == {"Hans Müler"}  # a copy, until the job runs

    assert refresh_customer_labels(db_session) == len(rows)

    for row in _rows(db_session, customer["id"]):
        assert row.customer_label == "Hans Müller"
        assert row.customer_denorm_refreshed_at > then
        assert row.updated_at > then
        if not isinstance(row, SalesDeal):
            assert row.customer_language == "fr"  # same stamp, same refresh
            assert row.version == versions[row.id]  # a copy no client writes: no stale-version 409 for the seller

    headers = _as(seller)
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=headers).json()["customerLabel"] == "Hans Müller"
    assert client.get(f"/v1/sales/contracts/{contract['id']}", headers=headers).json()["customerLabel"] == "Hans Müller"


@pytest.mark.parametrize("status", [ContractStatus.CONFIRMED, ContractStatus.INVOICED, ContractStatus.CANCELLED])
def test_a_signed_contract_and_its_deal_row_keep_the_name_the_buyer_signed_under(client, db_session, status):
    seller, customer = _setup(client, firstName="Clara", lastName="Brunner")
    contract = _contract_for(client, seller, customer)
    _sign(db_session, contract["id"], status)
    then = _backdate(db_session, _rows(db_session, customer["id"]), hours=30)

    _rename(db_session, customer["id"], last_name="Weber", language=Language.IT)
    assert refresh_customer_labels(db_session) == 0

    for row in _rows(db_session, customer["id"]):
        assert row.customer_label == "Clara Brunner"
        # Checked and kept on purpose: restamped, updated_at held.
        assert row.customer_denorm_refreshed_at > then
        assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1
        if isinstance(row, SalesContract):
            assert row.customer_language == "de"


def _contract_from_offer(client, seller: dict, offer: dict) -> dict:
    response = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(seller))
    assert response.status_code == 201, response.text
    return response.json()


def test_the_offer_behind_a_signed_contract_keeps_the_signed_name_too(client, db_session):
    """Review finding: an offer keeps its status when a contract is made from
    it, so the status alone would relabel the read-only offer while its signed
    contract keeps the signed name (KAN-408: the two never disagree)."""

    seller, customer = _setup(client, firstName="Anna", lastName="Byron")
    offer = _offer_with(client, seller, customer)
    contract = _contract_from_offer(client, seller, offer)
    _sign(db_session, contract["id"], ContractStatus.CONFIRMED)

    _rename(db_session, customer["id"], last_name="Umbenannt")
    assert refresh_customer_labels(db_session) == 0

    for row in _rows(db_session, customer["id"]):
        assert row.customer_label == "Anna Byron", type(row).__name__


def test_an_offer_with_a_pending_contract_follows_with_it_and_keeps_the_name_once_signed(client, db_session):
    """Review finding (round 3): the offer behind a pending contract follows
    with it, so the two carry the same name when the contract is signed."""

    seller, customer = _setup(client, firstName="Paul", lastName="Frey")
    offer = _offer_with(client, seller, customer)
    contract = _contract_from_offer(client, seller, offer)

    _rename(db_session, customer["id"], last_name="Frei")
    refresh_customer_labels(db_session)
    assert {r.customer_label for r in _rows(db_session, customer["id"])} == {"Paul Frei"}

    _sign(db_session, contract["id"], ContractStatus.CONFIRMED)
    _rename(db_session, customer["id"], last_name="Freiherr")
    refresh_customer_labels(db_session)
    assert {r.customer_label for r in _rows(db_session, customer["id"])} == {"Paul Frei"}


def test_a_cancelled_offer_keeps_its_name(client, db_session):
    seller, customer = _setup(client, firstName="Nina", lastName="Gerber")
    offer = _offer_with(client, seller, customer)
    response = client.post(
        f"/v1/sales/offers/{offer['id']}/cancel",
        json={"reason": "Kunde hat abgesagt"},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text

    _rename(db_session, customer["id"], last_name="Steiner")
    refresh_customer_labels(db_session)

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(offer["id"])).customer_label == "Nina Gerber"


def test_a_current_label_is_only_restamped(client, db_session):
    seller, customer = _setup(client, firstName="Rey", lastName="Ortiz")
    _offer_with(client, seller, customer)
    then = _backdate(db_session, _rows(db_session, customer["id"]), hours=30)

    assert refresh_customer_labels(db_session) == 0

    for row in _rows(db_session, customer["id"]):
        assert row.customer_label == "Rey Ortiz"
        assert row.customer_denorm_refreshed_at > then
        assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_a_customer_id_naming_no_customer_and_a_merged_customer_are_left_alone(client, db_session):
    seller, customer = _setup(client, firstName="Gone", lastName="Kunde")
    dangling = _offer_with(client, seller, customer)
    merged_customer = _customer_for(client, seller, firstName="Merged", lastName="Kunde")
    merged_offer = _offer_with(client, seller, merged_customer)

    row = db_session.get(SalesOffer, uuid.UUID(dangling["id"]))
    row.customer_id = uuid.uuid4()
    merged = db_session.get(Customer, uuid.UUID(merged_customer["id"]))
    merged.lifecycle_status = CustomerLifecycleStatus.MERGED
    merged.last_name = "Renamed"
    then = _backdate(db_session, [row, db_session.get(SalesOffer, uuid.UUID(merged_offer["id"]))], hours=30)

    refresh_customer_labels(db_session)

    db_session.expire_all()
    for offer_id, label in ((dangling["id"], "Gone Kunde"), (merged_offer["id"], "Merged Kunde")):
        offer = db_session.get(SalesOffer, uuid.UUID(offer_id))
        assert offer.customer_label == label
        assert abs((offer.customer_denorm_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_job_walks_more_than_one_batch(client, db_session, monkeypatch):
    from app.sales.services import customer_label

    monkeypatch.setattr(customer_label, "_BATCH", 2)
    seller, customer = _setup(client, firstName="Ben", lastName="Roth")
    offers = [_offer_with(client, seller, customer) for _ in range(5)]
    _rename(db_session, customer["id"], last_name="Rothen")

    refresh_customer_labels(db_session)

    db_session.expire_all()
    assert {db_session.get(SalesOffer, uuid.UUID(o["id"])).customer_label for o in offers} == {"Ben Rothen"}


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_the_stalest_stamp_and_signed_work_is_restamped_not_relabelled(client, db_session):
    assert oldest_customer_label_age_seconds(db_session) is None

    seller, customer = _setup(client, firstName="Ida", lastName="Moser")
    offer = _offer_with(client, seller, customer)
    signed = _contract_from_offer(client, seller, offer)
    _sign(db_session, signed["id"], ContractStatus.CONFIRMED)
    _backdate(db_session, _rows(db_session, customer["id"]), hours=30)
    age = oldest_customer_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600

    _rename(db_session, customer["id"], last_name="Meier")
    refresh_customer_labels(db_session)

    # Signed work keeps its name but is restamped, so the alarm is quiet — and
    # the offer a cancelled contract frees comes back with a fresh stamp.
    assert oldest_customer_label_age_seconds(db_session) < 60
    assert {r.customer_label for r in _rows(db_session, customer["id"])} == {"Ida Moser"}


def test_the_worker_heartbeat_records_the_customer_label_age(client, db_session, monkeypatch):
    recorded = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.__setitem__(label, s))
    seller, customer = _setup(client)
    _offer_with(client, seller, customer)

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["sales.customer"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_customer_label_refresh_after_the_owner_labels_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("sales.owner_labels") < names.index("sales.customer_labels")
    assert names.index("sales.customer_labels") < names.index("reconciliation.run_all")


# --- locking: the job waits for nobody --------------------------------------------------


def test_an_offer_another_writer_holds_is_skipped_until_the_next_night(engine, client, db_session):
    _postgres_only(engine)
    seller, customer = _setup(client, firstName="Eva", lastName="Lang")
    held = _offer_with(client, seller, customer)
    free = _offer_with(client, seller, customer)
    _rename(db_session, customer["id"], last_name="Kurz")

    writer = sessionmaker(bind=engine)()
    try:
        writer.execute(text("SELECT 1 FROM sales_offer WHERE id = :id FOR UPDATE"), {"id": uuid.UUID(held["id"])})
        refresh_customer_labels(db_session)
    finally:
        writer.rollback()
        writer.close()

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(free["id"])).customer_label == "Eva Kurz"
    assert db_session.get(SalesOffer, uuid.UUID(held["id"])).customer_label == "Eva Lang"
    # The held offer's deal row waits with it: the row shows its live entity,
    # so the night's reconciliation never finds the two apart.
    held_deal = db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(held["id"]))).one()
    assert held_deal.customer_label == "Eva Lang"
    refresh_customer_labels(db_session)
    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(held["id"])).customer_label == "Eva Kurz"
    held_deal = db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(held["id"]))).one()
    assert held_deal.customer_label == "Eva Kurz"

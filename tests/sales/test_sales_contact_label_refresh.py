"""KAN-590 — a contact renamed after being attached to Sales' open work is
followed within a day.

Before this ticket only a merge relabelled Sales' rows (KAN-580): a contact
renamed later kept the old name on the Sales list, the offer and contract
headers and the next generated document's addressee. The nightly
`sales.contact_labels` job re-reads the label (and the correspondence
language) through `contact.public.get_contact_sales_labels`, with the
`sales.owner_labels` rules (KAN-404): `version` not bumped, a held row
skipped, an id naming no contact left alone.

Anto's ruling (2026-11-10, 1A): open offers, pending contracts and their deal
rows follow; a confirmed, invoiced or cancelled contract — and its deal row —
keeps the name the buyer signed under. `contact_locality` is left as it is.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app import worker
from app.core.daily_scheduler import _REGISTRY, registered_job_names
from app.contact.models.contact import Contact, ContactLifecycleStatus, Language
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.models.deal import SalesDeal
from app.sales.models.offer import SalesOffer
from app.sales.public import oldest_contact_label_age_seconds, refresh_contact_labels
from tests.contact.test_contact_vehicle_party_close_rule import _postgres_only
from tests.identity_support import create_dealership, create_user
from tests.platform.test_user_deactivation import _as, _contract_for, _contact_for, _offer_for


def _offer_with(client, seller: dict, contact: dict) -> dict:
    offer = _offer_for(client, seller)
    response = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"contactId": contact["id"]},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _rows(db, contact_id: str) -> list[SalesOffer | SalesContract | SalesDeal]:
    db.expire_all()
    cid = uuid.UUID(contact_id)
    return [
        row
        for model in (SalesOffer, SalesContract, SalesDeal)
        for row in db.scalars(select(model).where(model.contact_id == cid)).all()
    ]


def _backdate(db, rows, hours: int) -> dt.datetime:
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)
    for row in rows:
        row.contact_denorm_refreshed_at = then
        row.updated_at = then
    db.commit()
    return then


def _rename(db, contact_id: str, *, last_name: str, language: Language | None = None) -> None:
    """The contact context's own write, as its PATCH leaves the row."""

    contact = db.get(Contact, uuid.UUID(contact_id))
    contact.last_name = last_name
    if language is not None:
        contact.language = language
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
    contact = _contact_for(client, seller, **names)
    return seller, contact


def test_a_rename_reaches_open_offers_pending_contracts_and_their_deal_rows(client, db_session):
    seller, contact = _setup(client, firstName="Hans", lastName="Müler")
    offer = _offer_with(client, seller, contact)
    contract = _contract_for(client, seller, contact)
    rows = _rows(db_session, contact["id"])
    assert {type(row) for row in rows} == {SalesOffer, SalesContract, SalesDeal}
    assert {row.contact_label for row in rows} == {"Hans Müler"}
    versions = {row.id: row.version for row in rows if not isinstance(row, SalesDeal)}
    then = _backdate(db_session, rows, hours=30)

    _rename(db_session, contact["id"], last_name="Müller", language=Language.FR)
    assert {row.contact_label for row in _rows(db_session, contact["id"])} == {"Hans Müler"}  # a copy, until the job runs

    assert refresh_contact_labels(db_session) == len(rows)

    for row in _rows(db_session, contact["id"]):
        assert row.contact_label == "Hans Müller"
        assert row.contact_denorm_refreshed_at > then
        assert row.updated_at > then
        if not isinstance(row, SalesDeal):
            assert row.contact_language == "fr"  # same stamp, same refresh
            assert row.version == versions[row.id]  # a copy no client writes: no stale-version 409 for the seller

    headers = _as(seller)
    assert client.get(f"/v1/sales/offers/{offer['id']}", headers=headers).json()["contactLabel"] == "Hans Müller"
    assert client.get(f"/v1/sales/contracts/{contract['id']}", headers=headers).json()["contactLabel"] == "Hans Müller"


@pytest.mark.parametrize("status", [ContractStatus.CONFIRMED, ContractStatus.INVOICED, ContractStatus.CANCELLED])
def test_a_signed_contract_and_its_deal_row_keep_the_name_the_buyer_signed_under(client, db_session, status):
    seller, contact = _setup(client, firstName="Clara", lastName="Brunner")
    contract = _contract_for(client, seller, contact)
    _sign(db_session, contract["id"], status)
    then = _backdate(db_session, _rows(db_session, contact["id"]), hours=30)

    _rename(db_session, contact["id"], last_name="Weber", language=Language.IT)
    assert refresh_contact_labels(db_session) == 0

    for row in _rows(db_session, contact["id"]):
        assert row.contact_label == "Clara Brunner"
        # Checked and kept on purpose: restamped, updated_at held.
        assert row.contact_denorm_refreshed_at > then
        assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1
        if isinstance(row, SalesContract):
            assert row.contact_language == "de"


def _contract_from_offer(client, seller: dict, offer: dict) -> dict:
    response = client.post("/v1/sales/contracts", json={"offerId": offer["id"]}, headers=_as(seller))
    assert response.status_code == 201, response.text
    return response.json()


def test_the_offer_behind_a_signed_contract_keeps_the_signed_name_too(client, db_session):
    """Review finding: an offer keeps its status when a contract is made from
    it, so the status alone would relabel the read-only offer while its signed
    contract keeps the signed name (KAN-408: the two never disagree)."""

    seller, contact = _setup(client, firstName="Anna", lastName="Byron")
    offer = _offer_with(client, seller, contact)
    contract = _contract_from_offer(client, seller, offer)
    _sign(db_session, contract["id"], ContractStatus.CONFIRMED)

    _rename(db_session, contact["id"], last_name="Umbenannt")
    assert refresh_contact_labels(db_session) == 0

    for row in _rows(db_session, contact["id"]):
        assert row.contact_label == "Anna Byron", type(row).__name__


def test_an_offer_with_a_pending_contract_follows_with_it_and_keeps_the_name_once_signed(client, db_session):
    """Review finding (round 3): the offer behind a pending contract follows
    with it, so the two carry the same name when the contract is signed."""

    seller, contact = _setup(client, firstName="Paul", lastName="Frey")
    offer = _offer_with(client, seller, contact)
    contract = _contract_from_offer(client, seller, offer)

    _rename(db_session, contact["id"], last_name="Frei")
    refresh_contact_labels(db_session)
    assert {r.contact_label for r in _rows(db_session, contact["id"])} == {"Paul Frei"}

    _sign(db_session, contract["id"], ContractStatus.CONFIRMED)
    _rename(db_session, contact["id"], last_name="Freiherr")
    refresh_contact_labels(db_session)
    assert {r.contact_label for r in _rows(db_session, contact["id"])} == {"Paul Frei"}


def _contract_buyer(client, seller: dict, contract: dict, contact_id: str | None) -> dict:
    current = client.get(f"/v1/sales/contracts/{contract['id']}", headers=_as(seller)).json()
    response = client.patch(
        f"/v1/sales/contracts/{contract['id']}",
        json={"contactId": contact_id},
        headers={**_as(seller), "If-Match": str(current["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_offer_whose_contract_names_another_buyer_keeps_its_own_contacts_name(client, db_session):
    """Review finding (round 5): a pending contract's buyer can be replaced
    (ADR-083 amended) while the offer keeps its own; the offer's label stays
    the copy of its own contact (rule 2), never the contract's buyer."""

    seller, anna = _setup(client, firstName="Anna", lastName="Alt")
    bruno = _contact_for(client, seller, firstName="Bruno", lastName="Neu", language="fr")
    offer = _offer_with(client, seller, anna)
    contract = _contract_from_offer(client, seller, offer)
    _contract_buyer(client, seller, contract, bruno["id"])
    _rename(db_session, anna["id"], last_name="Altmann")

    refresh_contact_labels(db_session)

    db_session.expire_all()
    row = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert (str(row.contact_id), row.contact_label, row.contact_language) == (anna["id"], "Anna Altmann", "de")
    assert db_session.get(SalesContract, uuid.UUID(contract["id"])).contact_label == "Bruno Neu"
    body = client.get(f"/v1/sales/offers/{offer['id']}", headers=_as(seller)).json()
    assert (body["contactId"], body["contactLabel"]) == (anna["id"], "Anna Altmann")


def test_an_offer_whose_contract_lost_its_buyer_keeps_its_own_contacts_name(client, db_session):
    seller, anna = _setup(client, firstName="Anna", lastName="Alt")
    offer = _offer_with(client, seller, anna)
    contract = _contract_from_offer(client, seller, offer)
    _contract_buyer(client, seller, contract, None)

    refresh_contact_labels(db_session)

    db_session.expire_all()
    row = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert (row.contact_label, row.contact_language) == ("Anna Alt", "de")


def test_an_offer_without_a_buyer_is_never_labelled_from_its_contract(client, db_session):
    seller, bruno = _setup(client, firstName="Bruno", lastName="Neu")
    offer = _offer_for(client, seller)  # no buyer
    contract = _contract_from_offer(client, seller, offer)
    _contract_buyer(client, seller, contract, bruno["id"])

    refresh_contact_labels(db_session)

    db_session.expire_all()
    row = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert (row.contact_id, row.contact_label) == (None, None)


def test_a_cancelled_offer_keeps_its_name(client, db_session):
    seller, contact = _setup(client, firstName="Nina", lastName="Gerber")
    offer = _offer_with(client, seller, contact)
    response = client.post(
        f"/v1/sales/offers/{offer['id']}/cancel",
        json={"reason": "Kunde hat abgesagt"},
        headers={**_as(seller), "If-Match": str(offer["version"])},
    )
    assert response.status_code == 200, response.text

    _rename(db_session, contact["id"], last_name="Steiner")
    refresh_contact_labels(db_session)

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(offer["id"])).contact_label == "Nina Gerber"


def test_a_current_label_is_only_restamped(client, db_session):
    seller, contact = _setup(client, firstName="Rey", lastName="Ortiz")
    _offer_with(client, seller, contact)
    then = _backdate(db_session, _rows(db_session, contact["id"]), hours=30)

    assert refresh_contact_labels(db_session) == 0

    for row in _rows(db_session, contact["id"]):
        assert row.contact_label == "Rey Ortiz"
        assert row.contact_denorm_refreshed_at > then
        assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_a_contact_id_naming_no_contact_and_a_merged_contact_are_left_alone(client, db_session):
    seller, contact = _setup(client, firstName="Gone", lastName="Kunde")
    dangling = _offer_with(client, seller, contact)
    merged_contact = _contact_for(client, seller, firstName="Merged", lastName="Kunde")
    merged_offer = _offer_with(client, seller, merged_contact)

    row = db_session.get(SalesOffer, uuid.UUID(dangling["id"]))
    row.contact_id = uuid.uuid4()
    merged = db_session.get(Contact, uuid.UUID(merged_contact["id"]))
    merged.lifecycle_status = ContactLifecycleStatus.MERGED
    merged.last_name = "Renamed"
    then = _backdate(db_session, [row, db_session.get(SalesOffer, uuid.UUID(merged_offer["id"]))], hours=30)

    refresh_contact_labels(db_session)

    db_session.expire_all()
    for offer_id, label in ((dangling["id"], "Gone Kunde"), (merged_offer["id"], "Merged Kunde")):
        offer = db_session.get(SalesOffer, uuid.UUID(offer_id))
        assert offer.contact_label == label
        assert abs((offer.contact_denorm_refreshed_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_job_walks_more_than_one_batch(client, db_session, monkeypatch):
    from app.sales.services import contact_label

    monkeypatch.setattr(contact_label, "_BATCH", 2)
    seller, contact = _setup(client, firstName="Ben", lastName="Roth")
    offers = [_offer_with(client, seller, contact) for _ in range(5)]
    _rename(db_session, contact["id"], last_name="Rothen")

    refresh_contact_labels(db_session)

    db_session.expire_all()
    assert {db_session.get(SalesOffer, uuid.UUID(o["id"])).contact_label for o in offers} == {"Ben Rothen"}


def test_a_contract_held_when_the_job_passes_keeps_its_offer_in_step_even_when_signed_next(engine, client, db_session):
    """Review finding (round 4): the offer mirrors its contract's committed
    label, never the contact's directly — so a pending contract a writer
    held while the job passed, then signed before the next night, never
    leaves the offer on another name for good."""

    _postgres_only(engine)
    seller, contact = _setup(client, firstName="Paul", lastName="Frey")
    offer = _offer_with(client, seller, contact)
    contract = _contract_from_offer(client, seller, offer)
    _rename(db_session, contact["id"], last_name="Frei")

    writer = sessionmaker(bind=engine)()
    try:
        writer.execute(
            text("SELECT 1 FROM sales_contract WHERE id = :id FOR UPDATE"), {"id": uuid.UUID(contract["id"])}
        )
        refresh_contact_labels(db_session)
    finally:
        writer.rollback()
        writer.close()

    db_session.expire_all()
    assert db_session.get(SalesContract, uuid.UUID(contract["id"])).contact_label == "Paul Frey"
    assert db_session.get(SalesOffer, uuid.UUID(offer["id"])).contact_label == "Paul Frey"  # in step with it

    _sign(db_session, contract["id"], ContractStatus.CONFIRMED)
    refresh_contact_labels(db_session)
    assert {r.contact_label for r in _rows(db_session, contact["id"])} == {"Paul Frey"}


# --- the mirror pass, pinned (KAN-646) ---------------------------------------------------
#
# `_mirror_offers_to_contracts` is the one piece that keeps an offer and its
# contract on one name (KAN-408). KAN-590's review found by mutation that no
# test failed when it stopped copying the language, let `updated_at` move on
# its stamp-only path, stopped after one batch or dropped the
# (offer, contact) re-check. Each test below fails without its behaviour.


def _offer_row(db, offer: dict) -> SalesOffer:
    db.expire_all()
    return db.get(SalesOffer, uuid.UUID(offer["id"]))


def test_the_mirror_copies_the_language_even_when_the_name_did_not_change(client, db_session):
    seller, contact = _setup(client, firstName="Lea", lastName="Favre")
    offer = _offer_with(client, seller, contact)
    contract = _contract_from_offer(client, seller, offer)
    assert _offer_row(db_session, offer).contact_language == "de"

    _rename(db_session, contact["id"], last_name="Favre", language=Language.FR)  # the language alone
    refresh_contact_labels(db_session)

    assert db_session.get(SalesContract, uuid.UUID(contract["id"])).contact_language == "fr"
    row = _offer_row(db_session, offer)
    # The next document of the offer prints in the language its contract's does.
    assert (row.contact_label, row.contact_language) == ("Lea Favre", "fr")


def test_the_mirror_restamps_an_offer_already_in_step_without_moving_updated_at(client, db_session):
    seller, contact = _setup(client, firstName="Rey", lastName="Ortiz")
    offer = _offer_with(client, seller, contact)
    _contract_from_offer(client, seller, offer)
    then = _backdate(db_session, _rows(db_session, contact["id"]), hours=30)

    assert refresh_contact_labels(db_session) == 0

    row = _offer_row(db_session, offer)
    assert row.contact_label == "Rey Ortiz"
    assert row.contact_denorm_refreshed_at > then, "checked tonight"
    # Nothing the API returns changed, so the row does not read as edited tonight.
    assert abs((row.updated_at.replace(tzinfo=dt.UTC) - then).total_seconds()) < 1


def test_the_mirror_walks_more_than_one_batch(client, db_session, monkeypatch):
    """Stopping after the first batch would leave every mirrored offer past
    the first 500 on its old name for good."""

    from app.sales.services import contact_label

    monkeypatch.setattr(contact_label, "_BATCH", 2)
    seller, contact = _setup(client, firstName="Ben", lastName="Roth")
    offers = [_offer_with(client, seller, contact) for _ in range(5)]
    for offer in offers:
        _contract_from_offer(client, seller, offer)
    _rename(db_session, contact["id"], last_name="Rothen")

    refresh_contact_labels(db_session)

    assert [_offer_row(db_session, offer).contact_label for offer in offers] == ["Ben Rothen"] * 5


def test_a_buyer_replaced_after_the_mirror_locked_the_offer_is_never_copied_onto_it(engine, client, db_session):
    """The mirror picks its offers, then reads their contracts as committed
    now. A seller who replaces the pending contract's buyer between the two
    (ADR-083 amended) must not put the new buyer's name on the offer, which
    still names its own contact (rule 2): the (offer, contact) re-check."""

    _postgres_only(engine)
    seller, anna = _setup(client, firstName="Anna", lastName="Alt")
    bruno = _contact_for(client, seller, firstName="Bruno", lastName="Neu", language="fr")
    offer = _offer_with(client, seller, anna)
    contract = _contract_from_offer(client, seller, offer)
    replaced: list[bool] = []

    def replace_the_buyer(conn, cursor, statement, parameters, context, executemany):
        # Fires once, just before the mirror reads the contracts of the
        # offers it has locked. The writer touches the contract alone, as the
        # job holds no contract row.
        if replaced or not statement.startswith("SELECT sales_contract.offer_id, sales_contract.contact_id"):
            return
        replaced.append(True)
        writer = sessionmaker(bind=engine)()
        try:
            row = writer.get(SalesContract, uuid.UUID(contract["id"]))
            row.contact_id = uuid.UUID(bruno["id"])
            row.contact_label = "Bruno Neu"
            row.contact_language = "fr"
            writer.commit()
        finally:
            writer.close()

    event.listen(engine, "before_cursor_execute", replace_the_buyer)
    try:
        refresh_contact_labels(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", replace_the_buyer)

    assert replaced, "the mirror's contract read was never seen: the test no longer exercises the re-check"
    assert db_session.get(SalesContract, uuid.UUID(contract["id"])).contact_label == "Bruno Neu"
    row = _offer_row(db_session, offer)
    assert (str(row.contact_id), row.contact_label, row.contact_language) == (anna["id"], "Anna Alt", "de")


def test_the_mirror_leaves_a_merged_or_unknown_contacts_offer_untouched(client, db_session):
    """As the table passes do: Sales' merge consumer moves a merged
    contact's rows (KAN-185), and the stale stamp is what the sync-age alarm
    shows meanwhile. The mirror used to restamp such an offer while its
    contract and deal row stayed untouched."""

    seller, merged_contact = _setup(client, firstName="Merged", lastName="Kunde")
    merged_offer = _offer_with(client, seller, merged_contact)
    _contract_from_offer(client, seller, merged_offer)
    gone_contact = _contact_for(client, seller, firstName="Gone", lastName="Kunde")
    gone_offer = _offer_with(client, seller, gone_contact)
    gone_contract = _contract_from_offer(client, seller, gone_offer)

    merged = db_session.get(Contact, uuid.UUID(merged_contact["id"]))
    merged.lifecycle_status = ContactLifecycleStatus.MERGED
    nobody = uuid.uuid4()  # the offer and its contract still name the same buyer: one that no longer exists
    for model, row_id in ((SalesOffer, gone_offer["id"]), (SalesContract, gone_contract["id"])):
        db_session.get(model, uuid.UUID(row_id)).contact_id = nobody
    for deal in db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(gone_offer["id"]))).all():
        deal.contact_id = nobody
    db_session.commit()
    rows = [*_rows(db_session, merged_contact["id"]), *_rows(db_session, str(nobody))]
    assert {type(row) for row in rows} == {SalesOffer, SalesContract, SalesDeal}
    then = _backdate(db_session, rows, hours=30)

    assert refresh_contact_labels(db_session) == 0

    for row in [*_rows(db_session, merged_contact["id"]), *_rows(db_session, str(nobody))]:
        stamp = row.contact_denorm_refreshed_at.replace(tzinfo=dt.UTC)
        assert abs((stamp - then).total_seconds()) < 1, f"{type(row).__name__} was restamped"
        assert row.contact_label in ("Merged Kunde", "Gone Kunde")


# --- the sync-age alarm (rule 10) --------------------------------------------------------


def test_label_age_is_the_stalest_stamp_and_signed_work_is_restamped_not_relabelled(client, db_session):
    assert oldest_contact_label_age_seconds(db_session) is None

    seller, contact = _setup(client, firstName="Ida", lastName="Moser")
    offer = _offer_with(client, seller, contact)
    signed = _contract_from_offer(client, seller, offer)
    _sign(db_session, signed["id"], ContractStatus.CONFIRMED)
    _backdate(db_session, _rows(db_session, contact["id"]), hours=30)
    age = oldest_contact_label_age_seconds(db_session)
    assert 30 * 3600 <= age < 31 * 3600

    _rename(db_session, contact["id"], last_name="Meier")
    refresh_contact_labels(db_session)

    # Signed work keeps its name but is restamped, so the alarm is quiet; a row
    # that comes back into open work later (the offer a cancelled contract
    # frees, a copied offer) carries this fresh stamp.
    assert oldest_contact_label_age_seconds(db_session) < 60
    assert {r.contact_label for r in _rows(db_session, contact["id"])} == {"Ida Moser"}


def test_the_worker_heartbeat_records_the_contact_label_age(client, db_session, monkeypatch):
    recorded = {}
    monkeypatch.setattr(worker, "record_label_age_seconds", lambda label, s: recorded.__setitem__(label, s))
    seller, contact = _setup(client)
    _offer_with(client, seller, contact)

    worker._heartbeat(db_session, worker.InProcessTransport(lambda: db_session))

    assert recorded["sales.contact"] is not None


# --- scheduling --------------------------------------------------------------------------


@pytest.fixture
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_the_worker_registers_the_contact_label_refresh_after_the_owner_labels_before_reconciliation(_clean_registry):
    worker.register_daily_jobs()
    names = registered_job_names()
    assert names.index("sales.owner_labels") < names.index("sales.contact_labels")
    assert names.index("sales.contact_labels") < names.index("reconciliation.run_all")


# --- locking: the job waits for nobody --------------------------------------------------


def test_an_offer_another_writer_holds_is_skipped_until_the_next_night(engine, client, db_session):
    _postgres_only(engine)
    seller, contact = _setup(client, firstName="Eva", lastName="Lang")
    held = _offer_with(client, seller, contact)
    free = _offer_with(client, seller, contact)
    _rename(db_session, contact["id"], last_name="Kurz")

    writer = sessionmaker(bind=engine)()
    try:
        writer.execute(text("SELECT 1 FROM sales_offer WHERE id = :id FOR UPDATE"), {"id": uuid.UUID(held["id"])})
        refresh_contact_labels(db_session)
    finally:
        writer.rollback()
        writer.close()

    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(free["id"])).contact_label == "Eva Kurz"
    assert db_session.get(SalesOffer, uuid.UUID(held["id"])).contact_label == "Eva Lang"
    # The held offer's deal row waits with it: the row shows its live entity,
    # so the night's reconciliation never finds the two apart.
    held_deal = db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(held["id"]))).one()
    assert held_deal.contact_label == "Eva Lang"
    refresh_contact_labels(db_session)
    db_session.expire_all()
    assert db_session.get(SalesOffer, uuid.UUID(held["id"])).contact_label == "Eva Kurz"
    held_deal = db_session.scalars(select(SalesDeal).where(SalesDeal.offer_id == uuid.UUID(held["id"]))).one()
    assert held_deal.contact_label == "Eva Kurz"

"""KAN-831 (follow-up of KAN-806; ADR-232): a labour-rate agreement
follows a contact merge and refreshes its contact label.

- The consumer `platform.labour_rate_agreements_on_contact_merged`, composed
  in `app/labour_rate_agreement_merge.py` (contact sits above platform), moves
  the duplicate's agreements to the end of the survivor's merge chain, in
  platform's own transaction, idempotent by eventId.
- The survivor's agreement survives (Anto, 2026-11-25 and 2026-11-27, option
  A): once the survivor holds any agreement for a category in the Betrieb,
  every row of the duplicate for that category — same date, later rate, end —
  stays on the duplicate's id, set aside, and never answers `labour_rate_for`.
- The nightly `platform.labour_rate_agreement_labels` relabels a renamed contact.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.contact.models.contact import Contact
from app.core.audit_model import AuditEvent
from app.core.consumer import consume_once
from app.core.money import PriceEntry
from app.labour_rate_agreement_merge import _contacts_now as _agreement_contacts_now
from app.labour_rate_agreement_merge import (
    move_labour_rate_agreements_on_contact_merged,
)
from app.labour_rate_agreement_merge import refresh_agreement_contact_labels_daily as _refresh_agreement_contact_labels
from app.platform import public as platform
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.models.labour_rate import LabourRateAgreement, LabourRateAgreementKind
from app.platform.organisation.services import labour_rates as rates
from app.platform.organisation.services.labour_rate_settings import RATES_ENTERED_KEY
from app.worker import register_daily_jobs, register_handlers
from tests.cross_context.test_consumer_redelivery_idempotency import _RecordingTransport
from tests.identity_support import create_dealership
from tests.platform.test_kan806_labour_rates import NEXT_MONTH, TODAY, _answer, _category, _set
from tests.sales.test_sales_contact_merged_consumer import _contact, _merge

CONSUMER = "platform.labour_rate_agreements_on_contact_merged"


@pytest.fixture()
def betrieb(client, db_session):
    dealership_id = uuid.UUID(create_dealership(client))
    _set(db_session, dealership_id, RATES_ENTERED_KEY, "net")
    group_id = db_session.get(Dealership, dealership_id).dealer_group_id
    return dealership_id, group_id


def _agreement(db, dealership_id, category, contact, *, amount="120.00", valid_from=TODAY,
               kind=LabourRateAgreementKind.RATE):
    rate = kind is LabourRateAgreementKind.RATE
    row = rates.create_agreement(
        db, dealership_id=dealership_id,
        contact=rates.AgreementContact(id=contact.id, label=f"{contact.first_name} {contact.last_name}"),
        category_id=category.id, kind=kind, valid_from=valid_from,
        amount=Decimal(amount) if rate else None, entered=PriceEntry.NET if rate else None,
        actor_id=None, on=TODAY,
    )
    db.commit()
    return row


def _deliver(db, message) -> bool:
    return consume_once(db, message=message, consumer_name=CONSUMER, handler=move_labour_rate_agreements_on_contact_merged)


def _row(db, row) -> LabourRateAgreement:
    db.expire_all()
    return db.get(LabourRateAgreement, row.id)


def test_the_consumer_is_registered_for_contact_merged_and_the_label_job_before_reconciliation(monkeypatch):
    transport = _RecordingTransport()
    register_handlers(transport)
    assert transport.registrations[CONSUMER][0] == "contact.merged"

    names: list[str] = []
    monkeypatch.setattr("app.worker.register_daily_job", lambda name, job: names.append(name))
    register_daily_jobs()
    assert names.index("platform.labour_rate_agreement_labels") < names.index("reconciliation.run_all")


def test_a_merge_moves_the_duplicates_agreement_to_the_survivor_with_its_name(db_session, betrieb):
    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    row = _agreement(db_session, dealership_id, category, duplicate)
    version = row.version
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    # Before the consumer ran, the survivor as invoice recipient gets no agreement.
    assert _answer(db_session, dealership_id, category, recipient=survivor.id).found is False
    assert _deliver(db_session, message)
    db_session.commit()

    moved = _row(db_session, row)
    assert moved.contact_id == survivor.id
    assert moved.contact_label == "Survivor Keller"
    assert moved.version == version + 1
    assert moved.set_aside_at is None
    answer = _answer(db_session, dealership_id, category, recipient=survivor.id)
    assert answer.found and answer.amount == Decimal("120.0000")
    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == row.id, AuditEvent.action == "repoint_on_contact_merge")
    ).one()
    assert audit.before["contactId"] == str(duplicate.id) and audit.after["contactId"] == str(survivor.id)


def test_a_redelivery_moves_nothing_twice(db_session, betrieb):
    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    row = _agreement(db_session, dealership_id, category, duplicate)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    assert _deliver(db_session, message)
    db_session.commit()
    version = _row(db_session, row).version
    assert not _deliver(db_session, message)
    # Even the handler run again by hand finds nothing left on the duplicate.
    move_labour_rate_agreements_on_contact_merged(db_session, message)
    db_session.commit()
    assert _row(db_session, row).version == version


def test_a_collision_keeps_the_survivors_agreement_and_sets_the_duplicates_aside(db_session, betrieb):
    """Anto, 2026-11-25 and 2026-11-27 (option A): the survivor holds an
    agreement for the category, so every row of the duplicate's for it is set
    aside — the same date and a later one alike; another category moves."""

    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    other = _category(db_session, dealership_id, name="Karosserie")
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    kept = _agreement(db_session, dealership_id, category, survivor, amount="110.00")
    colliding = _agreement(db_session, dealership_id, category, duplicate, amount="90.00")
    later = _agreement(db_session, dealership_id, category, duplicate, amount="95.00", valid_from=NEXT_MONTH)
    elsewhere = _agreement(db_session, dealership_id, other, duplicate, amount="100.00")
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    assert _deliver(db_session, message)
    db_session.commit()

    set_aside = _row(db_session, colliding)
    assert set_aside.contact_id == duplicate.id and set_aside.set_aside_at is not None
    assert set_aside.amount == Decimal("90.0000") and set_aside.withdrawn_at is None  # written once (ruling 178)
    assert _row(db_session, kept).contact_id == survivor.id
    later_row = _row(db_session, later)  # a later date of the same category: set aside too (option A)
    assert later_row.contact_id == duplicate.id and later_row.set_aside_at is not None
    assert _row(db_session, elsewhere).contact_id == survivor.id  # a category the survivor has none of: moves
    assert _answer(db_session, dealership_id, category, recipient=survivor.id).amount == Decimal("110.0000")
    assert _answer(db_session, dealership_id, category, recipient=survivor.id, on=NEXT_MONTH).amount == Decimal(
        "110.0000"
    )
    assert _answer(db_session, dealership_id, other, recipient=survivor.id).amount == Decimal("100.0000")
    # The set-aside row never answers, not even for the duplicate's id.
    assert _answer(db_session, dealership_id, category, recipient=duplicate.id).source != "agreement"
    # On the screen it is superseded, and listed only with the withdrawn ones.
    statuses = rates.agreement_statuses(rates.agreements_of(db_session, dealership_id=dealership_id, include_withdrawn=True), TODAY)
    assert statuses[set_aside.id] == rates.LabourRateStatus.SUPERSEDED
    assert set_aside.id not in {r.id for r in rates.agreements_of(db_session, dealership_id=dealership_id)}
    assert db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_id == colliding.id, AuditEvent.action == "set_aside_by_contact_merge")
    ).one()


def test_a_chain_merge_delivered_out_of_order_lands_on_the_end_of_the_chain(db_session, betrieb):
    """X→D is delivered after D→T: X's agreement lands on T, never on the merged D."""

    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    end = _contact(db_session, group_id, dealership_id, "End")
    middle = _contact(db_session, group_id, dealership_id, "Middle")
    first = _contact(db_session, group_id, dealership_id, "First")
    row = _agreement(db_session, dealership_id, category, first)
    first_into_middle = _merge(db_session, duplicate_id=first.id, survivor_id=middle.id, group_id=group_id)
    middle_into_end = _merge(db_session, duplicate_id=middle.id, survivor_id=end.id, group_id=group_id)

    assert _deliver(db_session, middle_into_end)
    assert _deliver(db_session, first_into_middle)
    db_session.commit()

    moved = _row(db_session, row)
    assert moved.contact_id == end.id and moved.contact_label == "End Keller"


def test_the_duplicates_end_never_ends_the_survivors_agreement(db_session, betrieb):
    """The review's case: the duplicate's agreement and its end from next month.
    Its end is set aside with its rate, so the survivor's agreement goes on."""

    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    _agreement(db_session, dealership_id, category, survivor, amount="110.00", valid_from=NEXT_MONTH)
    rate = _agreement(db_session, dealership_id, category, duplicate, amount="90.00")
    end = _agreement(db_session, dealership_id, category, duplicate, valid_from=NEXT_MONTH,
                     kind=LabourRateAgreementKind.END)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    assert _deliver(db_session, message)
    db_session.commit()

    assert {_row(db_session, r).contact_id for r in (rate, end)} == {duplicate.id}
    assert all(_row(db_session, r).set_aside_at is not None for r in (rate, end))
    on_next_month = _answer(db_session, dealership_id, category, recipient=survivor.id, on=NEXT_MONTH)
    assert on_next_month.source == "agreement" and on_next_month.amount == Decimal("110.0000")
    # Before the survivor's own agreement starts, the category rate applies (the
    # duplicate's 90 is set aside — the Betrieb re-enters it if it should apply).
    assert _answer(db_session, dealership_id, category, recipient=survivor.id).source != "agreement"


def test_without_a_survivor_agreement_the_duplicates_rate_and_end_both_move(db_session, betrieb):
    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    rate = _agreement(db_session, dealership_id, category, duplicate, amount="90.00")
    end = _agreement(db_session, dealership_id, category, duplicate, valid_from=NEXT_MONTH,
                     kind=LabourRateAgreementKind.END)
    message = _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id)

    assert _deliver(db_session, message)
    db_session.commit()

    assert {_row(db_session, r).contact_id for r in (rate, end)} == {survivor.id}
    assert _answer(db_session, dealership_id, category, recipient=survivor.id).amount == Decimal("90.0000")
    assert _answer(db_session, dealership_id, category, recipient=survivor.id, on=NEXT_MONTH).source != "agreement"


def test_the_nightly_job_relabels_a_renamed_contact_and_leaves_a_merged_one(db_session, betrieb):
    dealership_id, group_id = betrieb
    category = _category(db_session, dealership_id)
    insurer = _contact(db_session, group_id, dealership_id, "Insurer")
    survivor = _contact(db_session, group_id, dealership_id, "Survivor")
    duplicate = _contact(db_session, group_id, dealership_id, "Duplicate")
    renamed = _agreement(db_session, dealership_id, category, insurer)
    _agreement(db_session, dealership_id, category, survivor, amount="110.00")
    set_aside = _agreement(db_session, dealership_id, category, duplicate, amount="90.00")
    _deliver(db_session, _merge(db_session, duplicate_id=duplicate.id, survivor_id=survivor.id, group_id=group_id))
    db_session.commit()
    db_session.get(Contact, insurer.id).last_name = "Versicherungen"
    db_session.commit()
    version = _row(db_session, renamed).version
    stamp = _row(db_session, set_aside).contact_label_refreshed_at

    _refresh_agreement_contact_labels(db_session)

    relabelled = _row(db_session, renamed)
    assert relabelled.contact_label == "Insurer Versicherungen"
    assert relabelled.version == version  # a copy, refreshed on purpose: no version bump
    left = _row(db_session, set_aside)
    assert left.contact_label == "Duplicate Keller" and left.contact_label_refreshed_at == stamp
    report = platform.refresh_agreement_contact_labels(db_session, labels_of=_agreement_contacts_now(db_session))
    assert report.changed == [] and report.restamped == 2 and len(report.unresolved) == 1
    assert platform.oldest_agreement_contact_label_age_seconds(db_session) is not None

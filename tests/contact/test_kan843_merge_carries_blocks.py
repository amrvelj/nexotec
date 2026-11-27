"""KAN-843 (Anto, 2026-11-26, option 1): a contact merge carries every block
either side held onto the survivor — the credit block (ADR-065) and
«Gesperrt» (KAN-810), each with its reason. Before, both stayed on the merged
tombstone and a blocked person merged into a clean record could be ordered
and contracted again. The merge's audit row names what moved; the survivor's
History has the block change of its own (reason `contact_merged`)."""

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.contact.models.contact import Contact
from app.contact.schemas.contact import ContactCreate, ContactEmailCreate
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from app.contact.services.contact import create_contact, set_block, set_credit_block
from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError
from tests.contact.test_contact_vehicle_party_close_rule import (
    _postgres_only,
    _run,
    _wait_until_a_backend_waits_on_a_lock,
)


def _customer(db, group_id: uuid.UUID, first_name: str) -> Contact:
    return create_contact(
        db,
        group_id=group_id,
        data=ContactCreate(
            contact_type="individual",
            language="de",
            first_name=first_name,
            last_name="Muster",
            emails=[
                ContactEmailCreate(
                    email_type="personal", email_address=f"{first_name.lower()}-{uuid.uuid4().hex[:6]}@example.ch", is_primary=True
                )
            ],
        ),
        actor_id=uuid.uuid4(),
        acting_dealership_id=None,
        dealership_id=uuid.uuid4(),
    )


def _credit_block(db, contact: Contact, reason: str) -> None:
    set_credit_block(db, contact=contact, blocked=True, reason=reason, actor_id=uuid.uuid4(), acting_dealership_id=None)


def _gesperrt(db, contact: Contact, reason: str) -> None:
    set_block(db, contact=contact, blocked=True, reason=reason, actor_id=uuid.uuid4(), acting_dealership_id=None)


def _merge(db, duplicate_id: uuid.UUID, survivor_id: uuid.UUID, group_id: uuid.UUID) -> None:
    loaded = contact_common_service.get_contact_or_404(db, group_id, duplicate_id)
    contact_merge_service.merge_contact(
        db, contact=loaded, duplicate_of_contact_id=survivor_id, actor_id=uuid.uuid4(), acting_dealership_id=None
    )


def _audit(db, contact_id: uuid.UUID, action: str) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == contact_id, AuditEvent.action == action).order_by(AuditEvent.id)
        ).all()
    )


def _reloaded(db, contact_id: uuid.UUID) -> Contact:
    db.expire_all()
    contact = db.get(Contact, contact_id)
    assert contact is not None
    return contact


def test_a_merge_carries_the_duplicates_credit_block_onto_a_clean_survivor(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    _credit_block(db_session, duplicate, "Zahlungsverzug seit März")
    placed_at = _reloaded(db_session, duplicate.id).credit_blocked_at
    survivor_version = _reloaded(db_session, survivor.id).version

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert kept.credit_block is True
    assert kept.credit_block_reason == "Zahlungsverzug seit März"
    assert kept.credit_blocked_at == placed_at
    assert kept.blocked is False
    assert kept.version == survivor_version + 1
    merge_row = _audit(db_session, duplicate.id, "merge")[-1]
    assert merge_row.after["creditBlockCarried"] is True
    assert merge_row.after["creditBlockReasonCarried"] == "Zahlungsverzug seit März"
    assert "blockedCarried" not in merge_row.after
    [survivor_row] = _audit(db_session, survivor.id, "credit_block_changed")
    assert survivor_row.reason == "contact_merged"
    assert survivor_row.before == {"creditBlock": False, "creditBlockReason": None}
    assert survivor_row.after == {"creditBlock": True, "creditBlockReason": "Zahlungsverzug seit März"}


def test_a_merge_carries_the_duplicates_gesperrt_onto_a_clean_survivor(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    _gesperrt(db_session, duplicate, "Hausverbot")
    placed_at = _reloaded(db_session, duplicate.id).blocked_at

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert kept.blocked is True
    assert kept.blocked_reason == "Hausverbot"
    assert kept.blocked_at == placed_at
    assert kept.credit_block is False
    merge_row = _audit(db_session, duplicate.id, "merge")[-1]
    assert merge_row.after["blockedCarried"] is True
    assert merge_row.after["blockedReasonCarried"] == "Hausverbot"
    assert "creditBlockCarried" not in merge_row.after
    [survivor_row] = _audit(db_session, survivor.id, "block_changed")
    assert survivor_row.reason == "contact_merged"
    assert survivor_row.after == {"blocked": True, "blockedReason": "Hausverbot"}
    # The tombstone keeps its own record of the block (history).
    assert _reloaded(db_session, duplicate.id).blocked is True


def test_a_block_both_sides_hold_keeps_the_survivors_time_and_both_reasons(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    _gesperrt(db_session, survivor, "Drohung am Schalter")
    _gesperrt(db_session, duplicate, "Hausverbot")
    survivor_placed_at = _reloaded(db_session, survivor.id).blocked_at

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert kept.blocked is True
    assert kept.blocked_reason == "Drohung am Schalter · Hausverbot"
    assert kept.blocked_at == survivor_placed_at
    survivor_rows = _audit(db_session, survivor.id, "block_changed")
    assert survivor_rows[-1].before == {"blocked": True, "blockedReason": "Drohung am Schalter"}


def test_the_same_reason_on_both_sides_is_not_repeated_and_changes_nothing(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    _credit_block(db_session, survivor, "Betreibung")
    _credit_block(db_session, duplicate, "Betreibung")
    survivor_version = _reloaded(db_session, survivor.id).version

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert kept.credit_block_reason == "Betreibung"
    assert kept.version == survivor_version
    assert len(_audit(db_session, survivor.id, "credit_block_changed")) == 1  # its own placing only
    assert _audit(db_session, duplicate.id, "merge")[-1].after["creditBlockCarried"] is True


def test_two_long_reasons_are_cut_to_the_column_and_kept_whole_in_the_audit(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    _gesperrt(db_session, survivor, "S" * 400)
    _gesperrt(db_session, duplicate, "D" * 400)

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert len(kept.blocked_reason) == 500
    assert kept.blocked_reason.startswith("S" * 400 + " · D")
    assert kept.blocked_reason.endswith("…")
    assert _audit(db_session, duplicate.id, "merge")[-1].after["blockedReasonCarried"] == "D" * 400


def test_a_merge_of_unblocked_contacts_leaves_the_survivor_untouched(db_session):
    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    survivor_version = _reloaded(db_session, survivor.id).version

    _merge(db_session, duplicate.id, survivor.id, group_id)

    kept = _reloaded(db_session, survivor.id)
    assert (kept.credit_block, kept.blocked, kept.version) == (False, False, survivor_version)
    after = _audit(db_session, duplicate.id, "merge")[-1].after
    assert not any(key.endswith("Carried") for key in after)


@pytest.mark.parametrize("kind", ["credit", "gesperrt"])
def test_a_lift_read_before_the_merge_is_refused_not_an_overwrite_of_the_carried_block(engine, db_session, kind):
    """A lift on the survivor that was read before the merge (its If-Match
    passed then) must not silently drop the block the merge carried over:
    it takes the contact row and re-checks the version, a 409."""

    group_id = uuid.uuid4()
    duplicate, survivor = _customer(db_session, group_id, "Dora"), _customer(db_session, group_id, "Sven")
    (_credit_block if kind == "credit" else _gesperrt)(db_session, duplicate, "Zahlungsverzug")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as reader:
        stale = contact_common_service.get_contact_or_404(reader, group_id, survivor.id)
        with factory() as merger:
            _merge(merger, duplicate.id, survivor.id, group_id)
        lift = set_credit_block if kind == "credit" else set_block
        with pytest.raises(ConflictError):
            lift(reader, contact=stale, blocked=False, reason=None, actor_id=uuid.uuid4(), acting_dealership_id=None)
        reader.rollback()

    kept = _reloaded(db_session, survivor.id)
    assert (kept.credit_block if kind == "credit" else kept.blocked) is True


def test_two_merges_into_one_survivor_both_carrying_a_block_never_deadlock(engine, db_session, monkeypatch):
    """X→T holds both contact rows and is paused just before it carries X's
    block onto T. Y→T, also carrying a block, waits for T's row (since KAN-843
    a merge takes its survivor FOR NO KEY UPDATE up front). With the old FOR
    SHARE, Y would take T too and wait to write it, X would then wait for
    Y's share: a deadlock, one merge a 500. Both commit, one after the other,
    and T carries both reasons."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    x, y, t = (_customer(db_session, group_id, name) for name in ("Xaver", "Yvonne", "Tina"))
    _gesperrt(db_session, x, "Grund X")
    _gesperrt(db_session, y, "Grund Y")
    x_id, y_id, t_id = x.id, y.id, t.id
    reached, release = threading.Event(), threading.Event()
    real_carry = contact_merge_service._carry_blocks

    def _pause_then_carry(db, **kwargs):
        if threading.current_thread().name == "merge-x-into-t":
            reached.set()
            release.wait(timeout=10)
        return real_carry(db, **kwargs)

    monkeypatch.setattr(contact_merge_service, "_carry_blocks", _pause_then_carry)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _merge_in(duplicate_id):
        def _go():
            with factory() as session:
                _merge(session, duplicate_id, t_id, group_id)

        return _go

    first = _run("merge-x-into-t", errors, _merge_in(x_id))
    try:
        assert reached.wait(timeout=10), "merge X→T never reached its pause"
        second = _run("merge-y-into-t", errors, _merge_in(y_id))
        _wait_until_a_backend_waits_on_a_lock(engine)  # Y→T waits on X
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
    finally:
        release.set()

    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert _reloaded(db_session, t_id).blocked_reason == "Grund X · Grund Y"

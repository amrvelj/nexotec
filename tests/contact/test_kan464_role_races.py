"""KAN-464 PR-1 — the role rules hold under concurrency (Postgres only).

The customer role carries the lifecycle status, the credit block and the
advisor (Q1). A role change, a PATCH of one of those fields and a credit
block take the contact row FOR UPDATE and re-read it (`services/contact.py::
_lock_for_role_change`), so one cannot slip past another:

- a role removal waiting on a credit block (or any write) that commits in
  between is refused with the If-Match conflict, never applied to a row it
  did not judge;
- a role PATCH racing a merge that brings the same role waits for the merge
  and sees the moved role: no 500, one open row per role.
"""

import threading
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.contact.models.contact import ContactRole, Contact, ContactRoleAssignment, ContactType, Language
from app.contact.schemas.contact import ContactUpdate
from app.contact.services import contact as contact_service
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from app.contact.services import roles as roles_service
from tests.contact.test_contact_vehicle_party_close_rule import (
    _postgres_only,
    _run,
    _wait_until_a_backend_waits_on_a_lock,
)


def _contact(db_session, group_id: uuid.UUID, name: str, roles: tuple[ContactRole, ...]) -> Contact:
    contact = Contact(
        group_id=group_id, contact_number=f"K-{uuid.uuid4().hex[:6]}", contact_type=ContactType.INDIVIDUAL,
        language=Language.DE, first_name=name, last_name="Muster",
    )
    db_session.add(contact)
    db_session.flush()
    for role in roles:
        db_session.add(ContactRoleAssignment(group_id=group_id, contact_id=contact.id, role=role))
    db_session.flush()
    return contact


def _patch(db, contact_id: uuid.UUID, group_id: uuid.UUID, data: ContactUpdate) -> None:
    loaded = contact_common_service.get_contact_or_404(db, group_id, contact_id)
    contact_service.update_contact(
        db, contact=loaded, data=data, actor_id=uuid.uuid4(), acting_dealership_id=None, dealership_id=uuid.uuid4()
    )


def _open_roles(db_session, contact_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    return sorted(
        r.value
        for r in db_session.scalars(
            select(ContactRoleAssignment.role).where(ContactRoleAssignment.contact_id == contact_id, ContactRoleAssignment.valid_to.is_(None))
        ).all()
    )


@pytest.mark.parametrize(
    "patch",
    [ContactUpdate(roles=[ContactRole.SUPPLIER]), ContactUpdate(lifecycle_status="inactive")],
    ids=["role-removal", "lifecycle-patch"],
)
def test_a_patch_waiting_on_a_write_that_commits_in_between_is_refused(engine, db_session, patch):
    """Another transaction holds the row (as a credit block does) and commits
    a new version while the PATCH waits: the PATCH re-reads under the lock
    and answers the If-Match conflict instead of judging a stale row."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id, "Lea", (ContactRole.CUSTOMER, ContactRole.SUPPLIER))
    db_session.commit()
    contact_id = contact.id
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    refusals: list[ConflictError] = []

    holder = factory()
    try:
        holder.execute(text("SELECT id FROM contact WHERE id = :id FOR UPDATE"), {"id": contact_id})

        def _patch_it():
            with factory() as session:
                try:
                    _patch(session, contact_id, group_id, patch)
                except ConflictError as exc:
                    refusals.append(exc)

        thread = _run("patch", errors, _patch_it)
        _wait_until_a_backend_waits_on_a_lock(engine)
        # The holder's write: a credit block, as set_credit_block makes it.
        holder.execute(
            text("UPDATE contact SET credit_block = true, credit_block_reason = 'Mahnung', version = version + 1 WHERE id = :id"),
            {"id": contact_id},
        )
        holder.commit()
        thread.join(timeout=10)
    finally:
        holder.close()

    assert not thread.is_alive()
    assert errors == []
    assert len(refusals) == 1 and "currentVersion" in (refusals[0].details or {})
    assert _open_roles(db_session, contact_id) == ["customer", "supplier"]
    assert db_session.get(Contact, contact_id).lifecycle_status.value == "prospect"


def test_a_role_patch_racing_a_merge_that_brings_the_same_role_sees_it(engine, db_session, monkeypatch):
    """X (a supplier) is merged into T (a contact) and paused before
    committing, holding T FOR SHARE. A PATCH giving T the supplier role waits
    on T's row, then reads the role the merge moved: nothing to insert, no
    500 and no 409 — T holds one open row per role."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    x = _contact(db_session, group_id, "Xaver", (ContactRole.SUPPLIER,))
    t = _contact(db_session, group_id, "Tina", (ContactRole.CUSTOMER,))
    db_session.commit()
    x_id, t_id = x.id, t.id

    reached, release = threading.Event(), threading.Event()
    real_repoint = roles_service.repoint_roles

    def _repoint_then_pause(db, **kwargs):
        result = real_repoint(db, **kwargs)
        if threading.current_thread().name == "merge":
            db.flush()
            reached.set()
            release.wait(timeout=10)
        return result

    monkeypatch.setattr(roles_service, "repoint_roles", _repoint_then_pause)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []

    def _merge():
        with factory() as session:
            loaded = contact_common_service.get_contact_or_404(session, group_id, x_id)
            contact_merge_service.merge_contact(
                session, contact=loaded, duplicate_of_contact_id=t_id, actor_id=uuid.uuid4(), acting_dealership_id=None
            )

    def _give_supplier():
        with factory() as session:
            _patch(session, t_id, group_id, ContactUpdate(roles=[ContactRole.CUSTOMER, ContactRole.SUPPLIER]))

    merge = _run("merge", errors, _merge)
    try:
        assert reached.wait(timeout=10), "the merge never reached its pause"
        patch = _run("patch", errors, _give_supplier)
        _wait_until_a_backend_waits_on_a_lock(engine)
        release.set()
        merge.join(timeout=10)
        patch.join(timeout=10)
    finally:
        release.set()

    assert not merge.is_alive() and not patch.is_alive()
    assert errors == []
    assert _open_roles(db_session, t_id) == ["customer", "supplier"]

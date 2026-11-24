"""KAN-390: a tag edit on a contact while another contact is merged into it.

`update_contact` inserts the new `contact_tag` rows; a merge into the same
contact moves the duplicate's tags onto it (`_repoint_contact_tags`). When
both name the same tag, the PATCH's insert hits
`uq_contact_tag_contact_id_tag`. Before KAN-390 that happened at the audit
row's flush, outside the `IntegrityError` guard around the commit, and the
request returned 500. Since KAN-372 the merge holds the survivor's row FOR
SHARE, so the PATCH waits for it and then always collides.
"""

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError
from app.contact.models.contact import Contact, ContactTag, ContactType, Language
from app.contact.schemas.contact import ContactUpdate
from app.contact.services import contact as contact_service
from app.contact.services import contact_common as contact_common_service
from app.contact.services import contact_merge as contact_merge_service
from tests.contact.test_contact_vehicle_party_close_rule import (
    _postgres_only,
    _run,
    _wait_until_a_backend_waits_on_a_lock,
)


def _contact(db_session, group_id: uuid.UUID, first_name: str, tags: tuple[str, ...] = ()) -> Contact:
    contact = Contact(
        group_id=group_id, contact_number=f"K-{uuid.uuid4().hex[:6]}", contact_type=ContactType.INDIVIDUAL,
        language=Language.EN, first_name=first_name, last_name="Muster",
    )
    db_session.add(contact)
    db_session.flush()
    for tag in tags:
        db_session.add(ContactTag(group_id=group_id, contact_id=contact.id, tag=tag))
    db_session.flush()
    return contact


def _merge(db, duplicate_id: uuid.UUID, survivor_id: uuid.UUID, group_id: uuid.UUID) -> None:
    loaded = contact_common_service.get_contact_or_404(db, group_id, duplicate_id)
    contact_merge_service.merge_contact(db, contact=loaded, duplicate_of_contact_id=survivor_id, actor_id=uuid.uuid4(), acting_dealership_id=None)


def _set_tags(db, contact_id: uuid.UUID, group_id: uuid.UUID, tags: list[str]) -> None:
    loaded = contact_common_service.get_contact_or_404(db, group_id, contact_id)
    contact_service.update_contact(
        db, contact=loaded, data=ContactUpdate(tags=tags), actor_id=uuid.uuid4(), acting_dealership_id=None, dealership_id=uuid.uuid4(),
    )


def _tags(db_session, contact_id: uuid.UUID) -> list[str]:
    db_session.expire_all()
    return sorted(db_session.scalars(select(ContactTag.tag).where(ContactTag.contact_id == contact_id)).all())


def _pause_merge(monkeypatch, *, after_the_tags: bool):
    """Pauses the merge thread after it has locked the survivor: after its tag
    rows reached the database (its audit row's flush), or just before it
    moves them."""

    reached, release = threading.Event(), threading.Event()

    def _pause():
        if threading.current_thread().name == "merge-x-into-t":
            reached.set()
            release.wait(timeout=10)

    if after_the_tags:
        real_record = contact_merge_service.record_audit_event

        def _record_then_pause(db, **kwargs):
            result = real_record(db, **kwargs)
            if kwargs["action"] == "merge":
                _pause()
            return result

        monkeypatch.setattr(contact_merge_service, "record_audit_event", _record_then_pause)
    else:
        real_repoint = contact_merge_service._repoint_contact_tags

        def _pause_then_repoint(db, **kwargs):
            _pause()
            return real_repoint(db, **kwargs)

        monkeypatch.setattr(contact_merge_service, "_repoint_contact_tags", _pause_then_repoint)
    return reached, release


@pytest.mark.parametrize("after_the_tags", [True, False], ids=["tags-moved", "tags-not-yet-moved"])
def test_a_tag_patch_racing_a_merge_that_brings_the_same_tag_is_a_409_never_a_500(
    engine, db_session, monkeypatch, after_the_tags
):
    """X (holding tag "vip") is merged into T and paused before committing,
    holding T's row FOR SHARE (KAN-372). A PATCH adding "vip" to T waits for
    it; once the merge commits, the PATCH's insert collides with the moved
    row. The PATCH is refused 409 `contact_tag_exists`; the merge stands, and
    T keeps the one "vip" row the merge gave it."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    x = _contact(db_session, group_id, "Xaver", tags=("vip",))
    t = _contact(db_session, group_id, "Tina", tags=("fleet",))
    db_session.commit()
    x_id, t_id = x.id, t.id
    reached, release = _pause_merge(monkeypatch, after_the_tags=after_the_tags)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    errors: list[Exception] = []
    refusals: list[ConflictError] = []

    def _merge_x_into_t():
        with factory() as session:
            _merge(session, x_id, t_id, group_id)

    def _patch_t():
        with factory() as session:
            try:
                _set_tags(session, t_id, group_id, ["fleet", "vip"])
            except ConflictError as exc:
                refusals.append(exc)

    merge = _run("merge-x-into-t", errors, _merge_x_into_t)
    try:
        assert reached.wait(timeout=10), "the merge never reached its pause"
        patch = _run("patch-t", errors, _patch_t)
        _wait_until_a_backend_waits_on_a_lock(engine)  # the PATCH waits on the merge
        release.set()
        merge.join(timeout=10)
        patch.join(timeout=10)
    finally:
        release.set()

    assert not merge.is_alive() and not patch.is_alive()
    assert errors == []
    assert [r.details["reason"] for r in refusals] == ["contact_tag_exists"]
    assert _tags(db_session, t_id) == ["fleet", "vip"]
    assert db_session.get(Contact, x_id).duplicate_of_contact_id == t_id


def test_a_tag_patch_without_a_race_still_replaces_the_tags(db_session):
    group_id = uuid.uuid4()
    t = _contact(db_session, group_id, "Tina", tags=("fleet", "old"))
    db_session.commit()

    _set_tags(db_session, t.id, group_id, ["fleet", "vip"])

    assert _tags(db_session, t.id) == ["fleet", "vip"]


def test_an_integrity_failure_that_is_not_the_tag_constraint_is_not_called_a_tag_collision(engine, db_session):
    """The KAN-390 guard names `contact_tag_exists` only for
    uq_contact_tag_contact_id_tag; any other constraint the flush trips
    (here a NOT NULL column the PATCH clears) keeps the generic 409. Since
    KAN-394 the schema refuses that null before the service, so the body is
    built with model_construct to reach the guard all the same."""

    _postgres_only(engine)
    group_id = uuid.uuid4()
    t = _contact(db_session, group_id, "Tina")
    db_session.commit()

    with pytest.raises(ConflictError) as refused:
        loaded = contact_common_service.get_contact_or_404(db_session, group_id, t.id)
        contact_service.update_contact(
            db_session, contact=loaded, data=ContactUpdate.model_construct(newsletter=None), actor_id=uuid.uuid4(), acting_dealership_id=None,
            dealership_id=uuid.uuid4(),
        )

    assert (refused.value.details or {}).get("reason") != "contact_tag_exists"


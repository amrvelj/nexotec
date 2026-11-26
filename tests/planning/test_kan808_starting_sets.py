"""KAN-808 PR-1d (ADR-222): a Betrieb that first holds Aftersales gets
Nexotec's planner starting sets — the technician and workplace types Anto gave
(2026-11-25) — once ever.

Each test starts from Platform's real fact (`set_entitlements`) and delivers it
through the worker's own registration. What the tests pin: the name in the
Betrieb's default correspondence language and the other three as translations,
a sample with its seed code and no figure; a redelivery, a re-grant and a
restatement copy nothing; another module copies nothing; a change or a
confirmation ends the sample; a name the Betrieb already uses is left alone;
two copies racing end in one."""

import threading
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit_model import AuditEvent
from app.core.i18n import SwissLanguage
from app.core.outbox_model import OutboxMessage
from app.core.outbox_transport import InProcessTransport
from app.core.permissions import ModuleCode
from app.core.processed_event_model import ProcessedEvent
from app.planning.models.lists import NAME_LENGTH, SEED_CODE_LENGTH, TechnicianType, WorkplaceType
from app.planning.models.starting_set import StartingSet
from app.planning.services import lists
from app.planning.services import starting_sets as sets
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.services import entitlement
from app.platform.organisation.services.entitlement_facts import (
    MODULE_ENTITLEMENT_GRANTED_EVENT,
    restate_module_granted,
)
from app.worker import register_handlers
from tests.identity_support import create_dealership
from tests.planning.test_kan808_booking_concurrency import _wait_until_a_backend_waits_on_a_lock
from tests.postgres_only import ROW_LOCKS

ANTO_TECHNICIAN_TYPES = [
    "Lernender",
    "Automobil-Fachmann",
    "Automobil-Mechatroniker",
    "Automobil-Diagnostiker",
    "Werkstattchef",
]
ANTO_WORKPLACE_TYPES = [
    "Hebebühne",
    "Arbeitsplatz ohne Hebebühne",
    "Diagnoseplatz",
    "Achsvermessung",
    "Reifenmontage",
    "Waschplatz",
]


def _factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _transport(engine) -> InProcessTransport:
    transport = InProcessTransport(_factory(engine))
    register_handlers(transport)
    return transport


def _betrieb(client, db: Session, *, language: SwissLanguage = SwissLanguage.DE) -> Dealership:
    dealership = db.get(Dealership, uuid.UUID(create_dealership(client)))
    dealership.default_correspondence_language = language
    db.commit()
    return dealership


def _grant(db: Session, dealership: Dealership, modules: set[ModuleCode]) -> list[OutboxMessage]:
    """Platform's real write; returns the facts it stated."""

    before = set(db.scalars(select(OutboxMessage.id)))
    entitlement.set_entitlements(db, dealership=dealership, modules=modules, actor_id=uuid.uuid4())
    return [
        message
        for message in db.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == MODULE_ENTITLEMENT_GRANTED_EVENT)
        )
        if message.id not in before
    ]


def _aftersales(messages: list[OutboxMessage]) -> OutboxMessage:
    [message] = [m for m in messages if m.payload["module"] == "aftersales"]
    return message


def _entries(db: Session, model, dealership_id) -> list:
    db.expire_all()
    return list(db.scalars(select(model).where(model.tenant_id == dealership_id).order_by(model.sort_order)))


def _markers(db: Session, dealership_id) -> int:
    return db.scalar(select(func.count()).select_from(StartingSet).where(StartingSet.tenant_id == dealership_id))


# --- the content -------------------------------------------------------------------


def test_the_sets_are_antos_names_in_four_languages_with_stable_codes():
    assert [entry.names[SwissLanguage.DE] for entry in sets.TECHNICIAN_TYPES] == ANTO_TECHNICIAN_TYPES
    assert [entry.names[SwissLanguage.DE] for entry in sets.WORKPLACE_TYPES] == ANTO_WORKPLACE_TYPES
    for prefix, entries in (("technician.", sets.TECHNICIAN_TYPES), ("workplace.", sets.WORKPLACE_TYPES)):
        codes = [entry.code for entry in entries]
        assert len(set(codes)) == len(codes)
        assert all(code.startswith(prefix) and len(code) <= SEED_CODE_LENGTH for code in codes)
        for entry in entries:
            assert set(entry.names) == set(SwissLanguage)
            assert all(
                name and name == lists.clean_name(name) and len(name) <= NAME_LENGTH for name in entry.names.values()
            )
        for language in SwissLanguage:
            # Unique per language, case ignored — or a copy would refuse its own entry.
            names = [entry.names[language].lower() for entry in entries]
            assert len(set(names)) == len(names), language


# --- the copy ----------------------------------------------------------------------


@pytest.mark.parametrize("language", [SwissLanguage.DE, SwissLanguage.FR])
def test_the_copy_arrives_in_four_languages_named_in_the_betriebs_language(client, db_session, engine, language):
    dealership = _betrieb(client, db_session, language=language)
    message = _aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES}))

    _transport(engine).deliver(message)

    for model, entries in ((TechnicianType, sets.TECHNICIAN_TYPES), (WorkplaceType, sets.WORKPLACE_TYPES)):
        rows = _entries(db_session, model, dealership.id)
        assert [(row.seed_code, row.name) for row in rows] == [(e.code, e.names[language]) for e in entries]
        for row, entry in zip(rows, entries, strict=True):
            assert row.is_sample is True
            assert row.name_translations == {lang.value: name for lang, name in entry.names.items() if lang != language}
            assert row.version == 1 and row.created_by is None
    marker = db_session.scalars(select(StartingSet).where(StartingSet.tenant_id == dealership.id)).one()
    assert (marker.edition, marker.source_event_id) == (sets.EDITION, message.id)


def test_the_seed_carries_no_figure_and_the_database_refuses_one_on_a_sample(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    _transport(engine).deliver(_aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES})))

    rows = _entries(db_session, TechnicianType, dealership.id)
    assert rows and all(row.rank is None and row.counted_time_factor is None for row in rows)
    rows[0].rank = 3
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_copy_is_audited_once_as_the_systems(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    message = _aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES}))
    _transport(engine).deliver(message)

    [row] = db_session.scalars(
        select(AuditEvent).where(AuditEvent.tenant_id == dealership.id, AuditEvent.action == "starting_set_copied")
    ).all()
    assert row.actor_id is None and row.entity_type == "planning_starting_set"
    assert row.after["edition"] == sets.EDITION and row.after["language"] == "de"
    assert row.after["sourceEventId"] == str(message.id)
    assert [entry["seedCode"] for entry in row.after["technicianTypes"]] == [e.code for e in sets.TECHNICIAN_TYPES]
    assert [entry["seedCode"] for entry in row.after["workplaceTypes"]] == [e.code for e in sets.WORKPLACE_TYPES]
    assert row.after["skipped"] == {"technicianTypes": [], "workplaceTypes": []}


# --- once per Betrieb, ever ---------------------------------------------------------


def test_a_redelivery_copies_nothing(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    message = _aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES}))
    transport = _transport(engine)
    transport.deliver(message)
    transport.deliver(message)

    assert len(_entries(db_session, WorkplaceType, dealership.id)) == len(sets.WORKPLACE_TYPES)
    assert _markers(db_session, dealership.id) == 1


def test_a_lost_processed_event_row_still_copies_nothing_twice(client, db_session, engine):
    """The marker, not only the harness: the same fact handled again after its
    processed_event row went (or a second consumer of it) copies nothing."""

    dealership = _betrieb(client, db_session)
    message = _aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES}))
    transport = _transport(engine)
    transport.deliver(message)
    db_session.query(ProcessedEvent).filter(ProcessedEvent.event_id == message.id).delete()
    db_session.commit()
    transport.deliver(message)

    assert len(_entries(db_session, TechnicianType, dealership.id)) == len(sets.TECHNICIAN_TYPES)
    assert _markers(db_session, dealership.id) == 1


def test_a_later_re_grant_or_a_restatement_copies_nothing_and_a_deleted_sample_stays_gone(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    transport = _transport(engine)
    transport.deliver(_aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES})))
    lift = next(row for row in _entries(db_session, WorkplaceType, dealership.id) if row.seed_code == "workplace.lift")
    lists.delete_entry(
        db_session,
        lists.WORKPLACE_TYPES,
        dealership_id=dealership.id,
        entry_id=lift.id,
        expected_version=lift.version,
        actor_id=uuid.uuid4(),
    )
    db_session.commit()

    _grant(db_session, dealership, set())  # withdrawn
    regrant = _aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES}))
    transport.deliver(regrant)
    before = set(db_session.scalars(select(OutboxMessage.id)))
    restate_module_granted(db_session, module=ModuleCode.AFTERSALES)
    db_session.commit()
    [restated] = [
        message
        for message in db_session.scalars(select(OutboxMessage).where(OutboxMessage.tenant_id == dealership.id))
        if message.id not in before
    ]
    transport.deliver(restated)

    codes = [row.seed_code for row in _entries(db_session, WorkplaceType, dealership.id)]
    assert codes == [e.code for e in sets.WORKPLACE_TYPES if e.code != "workplace.lift"]
    assert _markers(db_session, dealership.id) == 1


def test_a_grant_of_another_module_copies_nothing(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    messages = _grant(db_session, dealership, {ModuleCode.SALES, ModuleCode.PARTS})
    assert {m.payload["module"] for m in messages} == {"sales", "parts"}
    transport = _transport(engine)
    for message in messages:
        transport.deliver(message)

    assert _entries(db_session, TechnicianType, dealership.id) == []
    assert _entries(db_session, WorkplaceType, dealership.id) == []
    assert _markers(db_session, dealership.id) == 0


@ROW_LOCKS
def test_two_copies_racing_for_one_betrieb_end_in_one(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    factory = _factory(engine)
    first = factory()
    results: list[bool] = []
    try:
        assert sets.copy_starting_sets(first, dealership_id=dealership.id, source_event_id=None) is True

        def second() -> None:
            session = factory()
            try:
                results.append(sets.copy_starting_sets(session, dealership_id=dealership.id, source_event_id=None))
                session.commit()
            finally:
                session.close()

        thread = threading.Thread(target=second)
        thread.start()
        _wait_until_a_backend_waits_on_a_lock(engine)
        first.commit()
        thread.join(30)
    finally:
        first.close()

    assert results == [False]
    assert len(_entries(db_session, TechnicianType, dealership.id)) == len(sets.TECHNICIAN_TYPES)
    assert _markers(db_session, dealership.id) == 1


# --- the Betrieb's own entries --------------------------------------------------------


def test_a_name_the_betrieb_already_uses_is_left_alone(client, db_session, engine):
    dealership = _betrieb(client, db_session)
    actor = uuid.uuid4()
    own = lists.create_entry(
        db_session, lists.WORKPLACE_TYPES, dealership_id=dealership.id, actor_id=actor, name="hebebühne", sort_order=4
    )
    db_session.commit()

    _transport(engine).deliver(_aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES})))

    rows = _entries(db_session, WorkplaceType, dealership.id)
    assert rows[0].id == own.id
    assert (rows[0].name, rows[0].is_sample, rows[0].seed_code, rows[0].version) == ("hebebühne", False, None, 1)
    # The rest follows the Betrieb's own entries, in the set's order.
    assert [(row.seed_code, row.sort_order) for row in rows[1:]] == [
        (e.code, 5 + i) for i, e in enumerate(e for e in sets.WORKPLACE_TYPES if e.code != "workplace.lift")
    ]
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "starting_set_copied")).one()
    assert audit.after["skipped"] == {"technicianTypes": [], "workplaceTypes": ["workplace.lift"]}


@pytest.mark.parametrize("how", ["change", "confirm"])
def test_a_change_or_a_confirmation_ends_the_sample(client, db_session, engine, how):
    dealership = _betrieb(client, db_session)
    _transport(engine).deliver(_aftersales(_grant(db_session, dealership, {ModuleCode.AFTERSALES})))
    row = _entries(db_session, TechnicianType, dealership.id)[0]
    actor = uuid.uuid4()

    if how == "change":
        lists.change_entry(
            db_session,
            lists.TECHNICIAN_TYPES,
            dealership_id=dealership.id,
            entry_id=row.id,
            expected_version=row.version,
            actor_id=actor,
            changes={"rank": 1},
        )
    else:
        lists.confirm_entry(
            db_session,
            lists.TECHNICIAN_TYPES,
            dealership_id=dealership.id,
            entry_id=row.id,
            expected_version=row.version,
            actor_id=actor,
        )
    db_session.commit()

    row = db_session.get(TechnicianType, row.id)
    assert row.is_sample is False
    assert row.seed_code == "technician.apprentice"  # still says where it came from
    assert row.version == 2

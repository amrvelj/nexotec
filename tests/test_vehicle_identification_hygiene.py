"""KAN-593 — identification waterfall hygiene after KAN-547 PR 2.

Pins what the KAN-547 PR 2 review found unpinned: the conflict event never
carries a null Stammnummer, a plate answer whose second car has no
Typenschein is a one-row picker (never a choice), and two Betriebe storing
one new variant at the same moment both get the same stored row — the
loser's unique violation is answered by re-reading, within a bound.

The plate answers here are built by hand (`BuiltVehicleData`): the mock's
registry has no record without a Stammnummer or Typenschein. Nothing here
says anything about the real provider's behaviour.
"""

import datetime as dt
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.outbox_model import OutboxMessage
from app.integration.adapters.auto_i_dat_mock import _MASTER_DATA
from app.integration.adapters.base import BuiltVehicleData
from app.vehicle.models.catalogue import Brand, ModelVariant
from app.vehicle.models.configuration import ConfigurationMatchMethod
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.services import catalogue_sync
from app.vehicle.services import identification as svc
from app.vehicle.services.identification import IdentificationOutcome, IdentifierKind, ObservedIdentity

_CONFLICT_EVENT = "vehicle.plate_lookup.conflicted"


def _car(*, stammnummer: str | None, type_approval_number: str | None, brand: str = "Volkswagen") -> BuiltVehicleData:
    return BuiltVehicleData(
        fz_key=None, vin=None, stammnummer=stammnummer, plate="ZH424242",
        first_registration_date=dt.date(2021, 4, 1), type_approval_number=type_approval_number, model_year=2021,
        vehicle_kind_code="01", brand_name=brand, model_description="Golf", production_from=2020, production_to=None,
        variant=None,
    )


def _plate_answer(db_session, tenant_id: uuid.UUID, vehicles: list[BuiltVehicleData]):
    return svc._built_vehicles_result(
        db_session, tenant_id=tenant_id, actor_id=None, kind=IdentifierKind.KONTROLLSCHILD,
        match_method=ConfigurationMatchMethod.KONTROLLSCHILD, observed=ObservedIdentity(licence_plate="ZH424242"),
        vehicles=vehicles, provider_code="auto_i_dat_mock", plate="ZH424242",
    )


def _conflict_events(db_session) -> list[OutboxMessage]:
    return list(db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == _CONFLICT_EVENT)).all())


# --- the conflict event's payload -------------------------------------------------


def test_the_conflict_event_names_only_records_that_carry_a_stammnummer(db_session):
    """One Stammnummer under two Typenscheine, and a third car whose
    Stammnummer the account may not see: the third can contradict nothing,
    so the payload leaves it out instead of sending a null."""

    tenant_id = uuid.uuid4()
    result = _plate_answer(
        db_session, tenant_id,
        [
            _car(stammnummer="111222333", type_approval_number="1AB234"),
            _car(stammnummer="111222333", type_approval_number="3EF789"),
            _car(stammnummer=None, type_approval_number="2CD456"),
        ],
    )

    assert result.outcome == IdentificationOutcome.PLATE_RECORDS
    assert result.plate_records_conflict is True
    assert len(result.plate_records) == 3  # the picker still offers all three cars
    (event,) = _conflict_events(db_session)
    assert event.payload["plate"] == "ZH424242"
    assert event.payload["records"] == [
        {"stammnummer": "111222333", "typeApprovalNumber": "1AB234"},
        {"stammnummer": "111222333", "typeApprovalNumber": "3EF789"},
    ]


def test_an_answer_the_cache_cannot_hold_reports_its_conflict_on_every_lookup(db_session):
    """KAN-593, decided: no per-day dedupe. The answer above is not cached
    (whole or not at all), so the next lookup asks the provider again and
    reports again — one fact per provider answer, grouped by the plate's
    aggregate id."""

    tenant_id = uuid.uuid4()
    vehicles = [
        _car(stammnummer="111222333", type_approval_number="1AB234"),
        _car(stammnummer="111222333", type_approval_number="3EF789"),
        _car(stammnummer=None, type_approval_number="2CD456"),
    ]
    _plate_answer(db_session, tenant_id, vehicles)
    _plate_answer(db_session, tenant_id, vehicles)

    events = _conflict_events(db_session)
    assert len(events) == 2
    assert len({e.aggregate_id for e in events}) == 1
    assert db_session.scalar(select(func.count()).select_from(PlateLookupCacheEntry)) == 0


# --- a picker of one --------------------------------------------------------------


def test_two_cars_where_one_has_no_typenschein_are_a_one_row_picker_never_a_choice(db_session):
    """The car without a Typenschein cannot be continued at and is left out;
    the one left is still offered in the picker (the Wechselschild title),
    never chosen for the advisor and never written anywhere."""

    tenant_id = uuid.uuid4()
    result = _plate_answer(
        db_session, tenant_id,
        [_car(stammnummer="444555666", type_approval_number="2CD456"), _car(stammnummer="777888999", type_approval_number=None)],
    )

    assert result.outcome == IdentificationOutcome.PLATE_RECORDS
    assert [r.type_approval_number for r in result.plate_records] == ["2CD456"]
    assert result.plate_records_interchangeable is True
    assert result.plate_records_conflict is False
    assert result.variants == []
    assert _conflict_events(db_session) == []


# --- concurrent storing of one new variant ----------------------------------------


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="row-level blocking is Postgres-only")
def test_a_variant_another_session_is_storing_is_re_read_not_a_failure(db_session, engine):
    """The reviewer's two-session probe of KAN-547 PR 2, committed: one
    session holds an uncommitted insert of the brand and variant; the second
    stores the same variant, blocks on the unique key, and — once the first
    commits — answers with the row the first wrote."""

    master = _MASTER_DATA["FZ100002"]
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    holder = factory()
    held = catalogue_sync.upsert_model_variant(holder, provider_code="auto_i_dat_mock", master=master)
    holder.flush()

    outcome: dict[str, object] = {}
    backend_known = threading.Event()

    def _second() -> None:
        with factory() as session:
            try:
                outcome["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
                backend_known.set()
                (variant,) = catalogue_sync.store_seen_variants(session, provider_code="auto_i_dat_mock", masters=[master])
                outcome["variant_id"] = variant.id
            except Exception as exc:  # noqa: BLE001 - reported below, the assertion names it
                session.rollback()
                outcome["error"] = exc
            finally:
                backend_known.set()

    def _waits_on_a_lock(pid: object) -> bool:
        """KAN-655: Postgres' own word that the backend is blocked on a lock —
        a live thread alone could be slow, not waiting."""

        with engine.connect() as observer:
            return (
                observer.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
                )
                == "Lock"
            )

    second = threading.Thread(target=_second, name="second")
    try:
        second.start()
        assert backend_known.wait(timeout=10), "the second session never started"
        deadline = time.monotonic() + 10
        while not _waits_on_a_lock(outcome.get("pid")) and time.monotonic() < deadline and second.is_alive():
            time.sleep(0.05)
        assert _waits_on_a_lock(outcome.get("pid")), (
            "the second session should wait on the first's uncommitted insert (pg_stat_activity: no Lock wait)"
        )
        assert "variant_id" not in outcome, "the second session answered before the first committed"
        holder.commit()
        second.join(timeout=20)
    finally:
        holder.rollback()  # a no-op after the commit; releases the lock if the assertion failed
        holder.close()
        second.join(timeout=20)

    assert "error" not in outcome, outcome.get("error")
    assert outcome["variant_id"] == held.id
    assert db_session.scalar(select(func.count()).select_from(ModelVariant)) == 1
    assert db_session.scalar(select(func.count()).select_from(Brand)) == 1


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT …", {}, Exception("duplicate key value violates unique constraint"))


def test_a_variant_that_loses_several_races_in_a_row_is_still_stored(db_session, monkeypatch):
    """One upsert can lose a race on each shared row it writes (brand, model
    group, variant): each retry runs in a savepoint of its own, so the third
    loss in a row still ends in the stored row."""

    real = catalogue_sync.upsert_model_variant
    losses = {"left": catalogue_sync._CONCURRENT_UPSERT_ATTEMPTS - 1}

    def _losing(db, **kwargs):
        if losses["left"]:
            losses["left"] -= 1
            raise _integrity_error()
        return real(db, **kwargs)

    monkeypatch.setattr(catalogue_sync, "upsert_model_variant", _losing)

    (variant,) = catalogue_sync.store_seen_variants(db_session, provider_code="auto_i_dat_mock", masters=[_MASTER_DATA["FZ100002"]])

    assert variant.id is not None
    assert losses["left"] == 0


def test_the_retry_is_bounded(db_session, monkeypatch):
    """A violation that survives every attempt is raised, never retried in an
    open loop."""

    calls = {"n": 0}

    def _always_losing(db, **kwargs):
        calls["n"] += 1
        raise _integrity_error()

    monkeypatch.setattr(catalogue_sync, "upsert_model_variant", _always_losing)

    with pytest.raises(IntegrityError):
        catalogue_sync.store_seen_variants(db_session, provider_code="auto_i_dat_mock", masters=[_MASTER_DATA["FZ100002"]])
    assert calls["n"] == catalogue_sync._CONCURRENT_UPSERT_ATTEMPTS

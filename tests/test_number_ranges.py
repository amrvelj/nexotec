"""KAN-207 — number ranges: gapless per Betrieb per document type (D-A-17,
ADR-032), patterns, the current value read-only, frozen at the first number
(A-RULE-3) and changed after that only by a platform admin with a reason;
handed out by Platform inside the calling module's transaction (ADR-109); the
offer range continues each Betrieb's existing O- sequence (Anto, 2026-11-01).
"""

import datetime as dt
import importlib.util
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.permissions import ModuleCode
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from app.platform.models.module_entitlement import ModuleEntitlement
from app.platform.models.number_range import NumberRange, NumberRangeDocumentType
from app.platform.services import number_range as service
from app.sales.models.deal import SalesNumberSequence
from app.sales.services.offer import create_offer
from scripts import continue_offer_number_ranges
from tests.dealership_support import valid_uid

OFFER = NumberRangeDocumentType.OFFER
INVOICE = NumberRangeDocumentType.INVOICE
_MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/platform/2b478e186431_kan207_number_ranges.py"


def _dealership(db, *, modules=(ModuleCode.SALES,)) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db.add(group)
    db.flush()
    dealership = Dealership(
        id=uuid.uuid4(), dealer_group_id=group.id, legal_name="Garage AG", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    db.add(dealership)
    db.flush()
    for module in modules:
        db.add(ModuleEntitlement(dealership_id=dealership.id, module=module))
    db.commit()
    return dealership


def _range(db, dealership_id, document_type=OFFER) -> NumberRange:
    return db.scalars(
        select(NumberRange).where(NumberRange.tenant_id == dealership_id, NumberRange.document_type == document_type)
    ).one()


# --- the seeds and the pattern grammar -------------------------------------------------


def test_the_migration_seeds_what_the_service_declares():
    spec = importlib.util.spec_from_file_location("kan207_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    declared = {(t.value, seed.pattern, seed.yearly_reset) for t, seed in service.SEEDS.items()}
    assert set(module._SEEDS) == declared
    assert {t for t, _, _ in module._SEEDS} == {t.value for t in NumberRangeDocumentType}


def test_every_seed_is_a_valid_pattern_that_fits():
    for document_type, seed in service.SEEDS.items():
        parsed = service.parse_pattern(seed.pattern)
        assert parsed.length_with(0) <= service.MAX_NUMBER_LENGTH, document_type
        assert not seed.yearly_reset or parsed.has_year, document_type


@pytest.mark.parametrize(
    ("pattern", "problem"),
    [
        ("O-", "counter_missing"),
        ("O-{counter:6}-{counter:2}", "token_repeated"),
        ("{year}{year}-{counter:3}", "token_repeated"),
        ("O-{counter:0}", "unknown_token"),
        ("O-{counter:10}", "unknown_token"),
        ("O-{branch}-{counter:3}", "unknown_token"),
        ("O {counter:6}", "character"),
        ("O-{counter:6", "character"),
    ],
)
def test_a_malformed_pattern_is_refused_naming_the_problem(pattern, problem):
    with pytest.raises(UnprocessableEntityError) as refused:
        service.parse_pattern(pattern)
    assert refused.value.details == {"reason": "number_pattern_invalid", "problem": problem}


def test_a_pattern_renders_year_location_code_and_padded_counter():
    parsed = service.parse_pattern("R-{location_code}/{year}-{counter:4}")
    assert parsed.render(counter=7, year=2026, location_code="ZH1") == "R-ZH1/2026-0007"


def test_the_save_rules(db_session):
    dealership = _dealership(db_session)
    kwargs = {"dealership_id": dealership.id, "start_value": 1, "yearly_reset": False}

    with pytest.raises(UnprocessableEntityError) as refused:
        service.validate(db_session, document_type=OFFER, pattern="O-{location_code}-{counter:4}", **kwargs)
    assert refused.value.details["reason"] == "number_pattern_location_unavailable"  # an offer knows no site yet

    with pytest.raises(UnprocessableEntityError) as refused:
        service.validate(db_session, document_type=INVOICE, pattern="RECHNUNG-{year}-{counter:9}", **kwargs)
    assert refused.value.details["reason"] == "number_pattern_too_long"

    with pytest.raises(UnprocessableEntityError) as refused:
        service.validate(
            db_session, document_type=INVOICE, pattern="R-{counter:6}", dealership_id=dealership.id,
            start_value=1, yearly_reset=True,
        )
    assert refused.value.details["reason"] == "number_pattern_reset_needs_year"

    with pytest.raises(UnprocessableEntityError) as refused:
        service.validate(
            db_session, document_type=INVOICE, pattern="R-{counter:2}", dealership_id=dealership.id,
            start_value=100, yearly_reset=False,
        )
    assert refused.value.details["reason"] == "number_range_start_out_of_range"


def test_location_code_needs_a_coded_location_and_is_a_label_not_a_counter(db_session):
    dealership = _dealership(db_session)
    with pytest.raises(UnprocessableEntityError) as refused:
        service.validate(
            db_session, dealership_id=dealership.id, document_type=INVOICE, pattern="{location_code}-{counter:5}",
            start_value=1, yearly_reset=False,
        )
    assert refused.value.details["reason"] == "number_pattern_location_code_missing"

    zh = Location(tenant_id=dealership.id, name="Zürich", code="ZH1", location_types=[], is_active=True)
    be = Location(tenant_id=dealership.id, name="Bern", code="BE2", location_types=[], is_active=True)
    db_session.add_all([zh, be])
    db_session.commit()
    row = service.get_or_seed(db_session, dealership_id=dealership.id, document_type=INVOICE, lock=True)
    service.update_range(
        db_session, row=row, pattern="{location_code}-{counter:5}", start_value=None, yearly_reset=False,
        reason=None, actor_id=uuid.uuid4(), actor_is_platform_admin=False,
    )
    numbers = [
        service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE, location_id=zh.id),
        service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE, location_id=be.id),
    ]
    assert numbers == ["ZH1-00001", "BE2-00002"]  # one continuous sequence across the sites (D-A-17)
    with pytest.raises(ConflictError) as refused:
        service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE)
    assert refused.value.details["reason"] == "number_location_code_missing"


# --- allocation ------------------------------------------------------------------------


def test_the_first_number_freezes_the_range_and_is_audited(db_session):
    dealership = _dealership(db_session)
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=OFFER) == "O-000001"
    db_session.commit()

    row = _range(db_session, dealership.id)
    assert (row.last_value, row.frozen_at is not None) == (1, True)
    [frozen] = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "number_range_frozen")).all()
    assert frozen.entity_id == row.id
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=OFFER) == "O-000002"


def test_a_rolled_back_document_gives_its_number_back(db_session):
    dealership = _dealership(db_session)
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=OFFER) == "O-000001"
    db_session.rollback()  # the document write failed

    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=OFFER) == "O-000001"


def test_concurrent_allocations_are_gapless_and_never_duplicate(db_session, engine):
    """N writers on N connections, each allocating and committing at once:
    the row lock serves them one after the other."""

    dealership = _dealership(db_session)
    service.seed_number_ranges(db_session, dealership_id=dealership.id, actor_id=None)
    db_session.commit()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    writers = 12
    start = threading.Barrier(writers)
    numbers: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def write() -> None:
        session = factory()
        try:
            start.wait()
            number = service.allocate_number(session, dealership_id=dealership.id, document_type=INVOICE)
            session.commit()
            with lock:
                numbers.append(number)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=write) for _ in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    year = service.swiss_year()
    assert sorted(numbers) == [f"R-{year}-{n:06d}" for n in range(1, writers + 1)]


def test_a_yearly_range_starts_again_in_a_new_year(db_session, monkeypatch):
    dealership = _dealership(db_session)
    monkeypatch.setattr(service, "swiss_year", lambda: 2026)
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE) == "R-2026-000001"
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE) == "R-2026-000002"
    monkeypatch.setattr(service, "swiss_year", lambda: 2027)
    assert service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE) == "R-2027-000001"


def test_a_range_out_of_digits_is_refused(db_session):
    dealership = _dealership(db_session)
    row = service.get_or_seed(db_session, dealership_id=dealership.id, document_type=INVOICE, lock=True)
    service.update_range(
        db_session, row=row, pattern="X{year}{counter:1}", start_value=9, yearly_reset=True, reason=None,
        actor_id=uuid.uuid4(), actor_is_platform_admin=False,
    )
    service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE)
    with pytest.raises(ConflictError) as refused:
        service.allocate_number(db_session, dealership_id=dealership.id, document_type=INVOICE)
    assert refused.value.details == {"reason": "number_range_exhausted"}


def test_a_new_betrieb_gets_its_eleven_ranges_unfrozen(db_session, client):
    from tests.identity_support import create_dealership

    created = uuid.UUID(create_dealership(client))
    rows = db_session.scalars(select(NumberRange).where(NumberRange.tenant_id == created)).all()
    assert {r.document_type for r in rows} == set(NumberRangeDocumentType)
    assert all(r.frozen_at is None and r.last_value is None for r in rows)


# --- the offer switched to its range, continuing Sales' sequence ----------------------


def test_offers_draw_from_the_range_and_continue_the_existing_sequence(db_session):
    dealership = _dealership(db_session)
    # Before KAN-207, Sales numbered this Betrieb's offers up to O-000123.
    db_session.add(SalesNumberSequence(tenant_id=dealership.id, series="offer", next_value=124))
    db_session.commit()

    first = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    second = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    assert (first.offer_number, second.offer_number) == ("O-000124", "O-000125")
    assert _range(db_session, dealership.id).last_value == 125
    assert db_session.get(SalesNumberSequence, (dealership.id, "offer")).next_value == 124  # never moved again


def test_a_betrieb_without_offers_starts_its_offer_range_at_one(db_session):
    dealership = _dealership(db_session)
    assert create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()).offer_number == "O-000001"


def test_the_script_shows_the_continued_value_before_the_next_offer(db_session):
    dealership = _dealership(db_session)
    db_session.add(SalesNumberSequence(tenant_id=dealership.id, series="offer", next_value=124))
    db_session.commit()

    dry = continue_offer_number_ranges.run(db_session, commit=False)
    assert dry.continued == [(dealership.id, 123)]
    assert db_session.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership.id)).all() == []

    continue_offer_number_ranges.run(db_session, commit=True)
    row = _range(db_session, dealership.id)
    assert (row.last_value, row.frozen_at) == (123, None)  # shown, still editable
    assert service.next_number_example(db_session, row) == "O-000124"
    again = continue_offer_number_ranges.run(db_session, commit=True)
    assert (again.continued, again.unchanged) == ([], 1)  # idempotent
    assert create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()).offer_number == "O-000124"


# --- the API ---------------------------------------------------------------------------


def _headers(dealership, *, manager=True, roles=frozenset(), user=None):
    token = create_access_token(
        user_id=user or uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        roles=roles, is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


def _staff(dealership):
    return _headers(dealership, manager=False, roles=frozenset({AccessRole.PLATFORM_ADMIN}))


def test_api_lists_the_ranges_of_the_modules_the_betrieb_holds(client, db_session):
    dealership = _dealership(db_session, modules=(ModuleCode.SALES, ModuleCode.FINANCE))
    response = client.get(
        f"/v1/dealerships/{dealership.id}/number-ranges", headers=_headers(dealership, manager=False, roles=frozenset({AccessRole.SALES}))
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["documentType"] for r in body["items"]] == [
        "offer", "order_confirmation", "sales_contract", "purchase_contract", "invoice", "credit_note", "dunning",
        "stock_number",
    ]
    offer = body["items"][0]
    assert (offer["pattern"], offer["nextNumber"], offer["lastValue"], offer["frozenAt"]) == ("O-{counter:6}", "O-000001", None, None)
    assert body["mayChangeFrozen"] is False


def test_api_another_betriebs_ranges_are_not_found(client, db_session):
    ours, theirs = _dealership(db_session), _dealership(db_session)
    assert client.get(f"/v1/dealerships/{theirs.id}/number-ranges", headers=_headers(ours)).status_code == 404


def test_api_the_manager_edits_before_the_freeze_and_the_change_is_audited(client, db_session):
    dealership = _dealership(db_session)
    user = uuid.uuid4()
    response = client.patch(
        f"/v1/dealerships/{dealership.id}/number-ranges/offer",
        json={"pattern": "OF-{year}-{counter:4}", "yearlyReset": True},
        headers={**_headers(dealership, user=user), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["nextNumber"] == f"OF-{service.swiss_year()}-0001"
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "number_range_updated")).one()
    assert (audit.actor_id, audit.before["pattern"], audit.after["pattern"]) == (user, "O-{counter:6}", "OF-{year}-{counter:4}")

    stale = client.patch(
        f"/v1/dealerships/{dealership.id}/number-ranges/offer", json={"startValue": 5},
        headers={**_headers(dealership), "If-Match": "1"},
    )
    assert stale.status_code == 409


def test_api_a_frozen_range_is_changed_by_a_platform_admin_with_a_reason_only(client, db_session):
    dealership = _dealership(db_session)
    service.allocate_number(db_session, dealership_id=dealership.id, document_type=OFFER)
    db_session.commit()
    version = _range(db_session, dealership.id).version
    path = f"/v1/dealerships/{dealership.id}/number-ranges/offer"

    by_manager = client.patch(path, json={"pattern": "A-{counter:6}"}, headers={**_headers(dealership), "If-Match": str(version)})
    assert by_manager.status_code == 409
    assert by_manager.json()["error"]["details"] == {"reason": "number_range_frozen"}

    without_reason = client.patch(path, json={"pattern": "A-{counter:6}"}, headers={**_staff(dealership), "If-Match": str(version)})
    assert without_reason.status_code == 422
    assert without_reason.json()["error"]["details"]["reason"] == "number_range_reason_required"

    with_reason = client.patch(
        path, json={"pattern": "A-{counter:6}", "reason": "Präfix laut Treuhänder falsch, Ticket 4711"},
        headers={**_staff(dealership), "If-Match": str(version)},
    )
    assert with_reason.status_code == 200, with_reason.text
    assert with_reason.json()["nextNumber"] == "A-000002"  # the counter goes on, never back
    audit = db_session.scalars(select(AuditEvent).where(AuditEvent.action == "number_range_frozen_changed")).one()
    assert audit.reason == "Präfix laut Treuhänder falsch, Ticket 4711"


def test_api_only_the_manager_or_staff_edit(client, db_session):
    dealership = _dealership(db_session)
    response = client.patch(
        f"/v1/dealerships/{dealership.id}/number-ranges/offer", json={"startValue": 5},
        headers={**_headers(dealership, manager=False, roles=frozenset({AccessRole.SALES})), "If-Match": "1"},
    )
    assert response.status_code == 403


def test_api_preview_is_the_next_number_or_the_saves_refusal(client, db_session):
    dealership = _dealership(db_session)
    path = f"/v1/dealerships/{dealership.id}/number-range-previews"
    ok = client.post(path, json={"documentType": "offer", "pattern": "OF-{counter:5}", "startValue": 40, "yearlyReset": False}, headers=_headers(dealership))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"nextNumber": "OF-00040"}
    refused = client.post(
        path, json={"documentType": "offer", "pattern": "O-{location_code}{counter:5}", "startValue": 1, "yearlyReset": False},
        headers=_headers(dealership),
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "number_pattern_location_unavailable"
    assert db_session.scalars(select(NumberRange).where(NumberRange.tenant_id == dealership.id)).all() == []


def test_api_a_range_of_a_module_not_held_is_not_found(client, db_session):
    dealership = _dealership(db_session)  # sales only
    response = client.patch(
        f"/v1/dealerships/{dealership.id}/number-ranges/invoice", json={"startValue": 5},
        headers={**_headers(dealership), "If-Match": "1"},
    )
    assert response.status_code == 404

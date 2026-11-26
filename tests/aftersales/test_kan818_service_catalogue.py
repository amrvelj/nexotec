"""KAN-818 (AS1-15): the service catalogue — packages with labour and text
positions, their times and «check needed» (section 3.10, ruling 182), the
time in the Betrieb's unit (ruling 183), names in up to four languages
(ruling 176), «from the catalogue» on a job (checks 44, 45, 46), the
catalogue's part in «copy configuration from a sister Betrieb», and the
nightly label refresh of its labour categories."""

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.aftersales.models.service_order import ServiceOrderPosition
from app.aftersales.models.service_package import ServicePackage, ServicePackagePosition
from app.aftersales.services import service_packages as catalogue
from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.money import PriceEntry
from app.core.permissions import ModuleCode
from app.platform.organisation.models.labour_rate import LabourRateAgreementKind
from app.platform.organisation.services import labour_rates as rates
from app.platform.organisation.services.labour_rate_settings import RATES_ENTERED_KEY
from tests.aftersales.service_order_support import (
    add_job,
    headers,
    make_betrieb,
    make_contact,
    new_order,
    set_setting,
    work_units,
)
from tests.platform.test_configuration_copy import _copy, _entitle, _pair


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _base(betrieb) -> str:
    return f"/v1/dealerships/{betrieb.id}/aftersales/service-packages"


def _manager(betrieb, **extra):
    return headers(betrieb, manager=True, **extra)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _labour(betrieb, time: str = "1.50", text: str = "Service nach Herstellervorgabe", **extra) -> dict:
    return {"kind": "labour", "text": text, "labourCategoryId": str(betrieb.category_id), "time": time, **extra}


def _package_body(betrieb, name: str = "Service 30'000 km", **extra) -> dict:
    return {
        "name": name,
        "positions": [
            _labour(betrieb, "1.50", checkNeeded=True),
            {"kind": "text", "text": "Ölfilter und Motoröl nach Vorgabe"},
        ],
        **extra,
    }


def _create(client, betrieb, body=None):
    return client.post(_base(betrieb), json=body or _package_body(betrieb), headers=_manager(betrieb))


def _created(client, betrieb, body=None) -> dict:
    response = _create(client, betrieb, body)
    assert response.status_code == 201, response.text
    return response.json()


def _put(client, betrieb, package: dict, body: dict):
    return client.put(
        f"{_base(betrieb)}/{package['id']}", json=body, headers=_manager(betrieb, if_match=package["version"])
    )


def _put_back(package: dict, **changes) -> dict:
    """The package as read, sent back whole (a PUT is the whole aggregate)."""

    positions = []
    for position in package["positions"]:
        item = {"id": position["id"], "kind": position["kind"], "text": position["text"],
                "checkNeeded": position["checkNeeded"]}
        if position["kind"] == "labour":
            item |= {"labourCategoryId": position["labourCategoryId"], "time": position["time"]["quantity"],
                     "timeUnit": position["time"]["unit"]}
        positions.append(item)
    return {"name": package["name"], "nameTranslations": package["nameTranslations"],
            "isActive": package["isActive"], "positions": positions, **changes}


def _put_on_job(client, betrieb, order: dict, job_id: str, package_id: str, **header_extra):
    return client.post(
        f"/v1/service-orders/{order['id']}/jobs/{job_id}/service-packages",
        json={"servicePackageId": package_id},
        headers=headers(betrieb, if_match=order["version"], **header_extra),
    )


def _order_with_job(client, db_session, betrieb):
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    return order, order["jobs"][0]["id"]


# --- criterion 1: a package, its positions, its refusals ----------------------------------------


def test_a_manager_creates_an_active_package_with_labour_and_text_positions(client, betrieb):
    package = _created(client, betrieb)

    assert package["isActive"] is True and package["version"] == 1
    labour, note = package["positions"]
    assert labour["kind"] == "labour" and labour["checkNeeded"] is True
    assert labour["labourCategoryId"] == str(betrieb.category_id) and labour["labourCategoryLabel"] == "Mechanik"
    assert labour["time"] == {"quantity": "1.50", "unit": "hours", "workUnitsPerHour": None}
    assert note["kind"] == "text" and note["time"] is None and note["checkNeeded"] is False


@pytest.mark.parametrize(
    ("body", "status", "reason"),
    [
        ({"name": "   ", "positions": []}, 422, "service_package_name_required"),
        ({"name": "Service", "positions": [{"kind": "labour", "text": "Arbeit", "labourCategoryId": "CAT"}]},
         422, "service_package_labour_time_required"),
        ({"name": "Service", "positions": [{"kind": "labour", "text": "Arbeit", "time": "1.00"}]},
         422, "service_package_labour_category_required"),
        ({"name": "Service", "positions": [{"kind": "labour", "text": "Arbeit", "labourCategoryId": "CAT",
                                             "time": "0"}]}, 422, "service_package_labour_time_not_positive"),
        ({"name": "Service", "positions": [{"kind": "labour", "text": "Arbeit", "labourCategoryId": "CAT",
                                             "time": "1.005"}]}, 422, "service_package_labour_time_too_many_decimals"),
        ({"name": "Service", "positions": [{"kind": "text", "text": "Hinweis", "time": "1.00"}]},
         422, "service_package_position_incomplete"),
        ({"name": "Service", "positions": [{"kind": "text", "text": "  "}]},
         422, "service_package_position_text_required"),
        ({"name": "Service", "positions": [{"kind": "labour", "text": "Arbeit", "time": "1.00",
                                             "labourCategoryId": str(uuid.uuid4())}]},
         422, "service_package_labour_category_unknown"),
    ],
)
def test_criterion_1_refusals(client, db_session, betrieb, body, status, reason):
    for position in body["positions"]:
        if position.get("labourCategoryId") == "CAT":
            position["labourCategoryId"] = str(betrieb.category_id)

    response = _create(client, betrieb, body)

    assert response.status_code == status, response.text
    assert _reason(response) == reason
    assert db_session.scalar(select(ServicePackage.id)) is None


def test_a_name_is_unique_in_the_betrieb_ignoring_case(client, betrieb):
    _created(client, betrieb, _package_body(betrieb, name="Bremsenservice"))

    response = _create(client, betrieb, _package_body(betrieb, name="bremsenservice"))

    assert response.status_code == 409 and _reason(response) == "service_package_name_taken"


def test_another_betriebs_labour_category_is_refused(client, db_session, betrieb):
    other = make_betrieb(client, db_session)

    response = _create(client, betrieb, {"name": "Service", "positions": [_labour(other)]})

    assert response.status_code == 422 and _reason(response) == "service_package_labour_category_unknown"


# --- criterion 6: the manager writes, the desk reads, the module gates ---------------------------


def test_the_desk_reads_and_only_the_manager_writes(client, betrieb):
    package = _created(client, betrieb)

    listed = client.get(_base(betrieb), headers=headers(betrieb))
    assert listed.status_code == 200 and [p["id"] for p in listed.json()["items"]] == [package["id"]]
    refused = client.post(_base(betrieb), json=_package_body(betrieb, name="Andere"), headers=headers(betrieb))
    assert refused.status_code == 403
    changed = client.put(
        f"{_base(betrieb)}/{package['id']}", json=_put_back(package, name="Neu"),
        headers=headers(betrieb, if_match=package["version"]),
    )
    assert changed.status_code == 403
    technician = client.get(_base(betrieb), headers=headers(betrieb, AccessRole.TECHNICIAN))
    assert technician.status_code == 403


def test_without_the_module_and_in_another_betrieb_it_is_not_found(client, db_session, betrieb):
    package = _created(client, betrieb)
    other = make_betrieb(client, db_session)

    # The module is in the token's entitlements (D-A-04): without it, 404 for everybody, the manager too.
    assert client.get(_base(betrieb), headers=_manager(betrieb, entitlements=frozenset({"sales"}))).status_code == 404
    assert client.post(_base(betrieb), json=_package_body(betrieb, name="X"),
                       headers=_manager(betrieb, entitlements=frozenset())).status_code == 404
    # The other Betrieb's token, this Betrieb's path: 404, never 403 (rule 7).
    assert client.get(_base(betrieb), headers=_manager(other)).status_code == 404
    assert client.get(f"{_base(other)}/{package['id']}", headers=_manager(other)).status_code == 404


# --- criterion 2: the time in the Betrieb's unit (ruling 183) ------------------------------------


def test_criterion_2_a_betrieb_on_work_units_types_and_keeps_the_time_in_work_units(client, db_session, betrieb):
    work_units(db_session, betrieb, 12)

    package = _created(client, betrieb, {"name": "Service", "positions": [_labour(betrieb, "9")]})

    assert package["positions"][0]["time"] == {"quantity": "9.00", "unit": "work_units", "workUnitsPerHour": "12.00"}
    refused = _create(client, betrieb, {"name": "Andere", "positions": [_labour(betrieb, "1.00", timeUnit="hours")]})
    assert refused.status_code == 409 and _reason(refused) == "service_package_time_unit_changed"


def test_a_change_of_the_unit_changes_no_package_and_a_time_sent_back_keeps_its_unit(client, db_session, betrieb):
    package = _created(client, betrieb)
    work_units(db_session, betrieb, 12)

    unchanged = _put(client, betrieb, package, _put_back(package, name="Service 30'000 km, gross"))

    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["positions"][0]["time"]["unit"] == "hours"
    stale = _put(client, betrieb, unchanged.json(), {
        **_put_back(unchanged.json()),
        "positions": [{**_put_back(unchanged.json())["positions"][0], "time": "2.00"}],
    })
    assert stale.status_code == 409 and _reason(stale) == "service_package_time_unit_changed"


# --- the aggregate: If-Match, nothing changed, the audit -----------------------------------------


def test_a_change_takes_if_match_and_a_change_that_changes_nothing_writes_nothing(client, db_session, betrieb):
    package = _created(client, betrieb)

    same = _put(client, betrieb, package, _put_back(package))
    assert same.status_code == 200 and same.json()["version"] == 1
    renamed = _put(client, betrieb, package, _put_back(package, name="Grosser Service"))
    assert renamed.status_code == 200 and renamed.json()["version"] == 2
    stale = _put(client, betrieb, package, _put_back(package, name="Kleiner Service"))
    assert stale.status_code == 409

    actions = db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_type == "service_package").order_by(AuditEvent.created_at)
    ).all()
    assert actions == ["create", "update"]


# --- check 46: the name in another language (ruling 176) -----------------------------------------


def test_check_46_a_name_per_language_and_as_typed_where_there_is_none(client, db_session, betrieb):
    package = _created(client, betrieb, _package_body(betrieb, nameTranslations={"fr": "Service 30'000 km FR",
                                                                                  "it": "  ", "en": "Service"}))

    # One name as typed, and only the languages given (an empty one is dropped): the screens read
    # the user's language from these, else the name as typed (render tests of both screens).
    assert package["name"] == "Service 30'000 km"
    assert package["nameTranslations"] == {"en": "Service", "fr": "Service 30'000 km FR"}
    listed = client.get(_base(betrieb), params={"active": "true"}, headers=headers(betrieb)).json()["items"]
    assert listed[0]["nameTranslations"] == {"en": "Service", "fr": "Service 30'000 km FR"}
    wrong = _create(client, betrieb, _package_body(betrieb, name="X", nameTranslations={"es": "Servicio"}))
    assert wrong.status_code == 422 and _reason(wrong) == "service_package_name_language"


# --- check 44: «from the catalogue» on a job -----------------------------------------------------


def test_check_44_a_package_brings_its_positions_times_and_check_needed_priced_at_the_rate_of_the_day(
    client, db_session, betrieb
):
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)
    # A typed position first: the package's positions follow it.
    typed = client.post(
        f"/v1/service-orders/{order['id']}/jobs/{job_id}/positions",
        json={"kind": "text", "text": "Kunde wartet"}, headers=headers(betrieb, if_match=order["version"]),
    )
    order = typed.json()

    response = _put_on_job(client, betrieb, order, job_id, package["id"])

    assert response.status_code == 201, response.text
    positions = response.json()["jobs"][0]["positions"]
    assert [p["text"] for p in positions] == ["Kunde wartet", "Service nach Herstellervorgabe",
                                             "Ölfilter und Motoröl nach Vorgabe"]
    labour, note = positions[1], positions[2]
    assert labour["kind"] == "labour_time" and labour["checkNeeded"] is True
    assert labour["time"]["quantity"] == "1.50" and labour["time"]["unit"] == "hours"
    assert labour["rateSource"] == "category_rate" and labour["unitPrice"]["amount"] == "150.0000"
    assert labour["lineNet"] == "225.00"  # 1.5 h × CHF 150.00, the rate of the order's day at its location
    assert note["kind"] == "text" and note["unitPrice"] is None and note["checkNeeded"] is False
    # One request, one change of the order (If-Match): its version moves once, whatever the package holds.
    assert response.json()["version"] == order["version"] + 1
    history = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(order["id"]),
                                 AuditEvent.action == "service_order_position_added")
    ).all()
    assert {event.after.get("servicePackageName") for event in history} >= {"Service 30'000 km"}


def test_check_44_with_work_units_9_aw_at_12_per_hour_and_chf_100_is_chf_75(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="100.00")
    work_units(db_session, betrieb, 12)
    package = _created(client, betrieb, {"name": "Inspektion", "positions": [_labour(betrieb, "9")]})
    order, job_id = _order_with_job(client, db_session, betrieb)

    response = _put_on_job(client, betrieb, order, job_id, package["id"])

    assert response.status_code == 201, response.text
    labour = response.json()["jobs"][0]["positions"][0]
    assert labour["time"]["quantity"] == "9.00" and labour["time"]["unit"] == "work_units"
    assert Decimal(labour["time"]["hours"]) == Decimal("0.75")
    assert labour["lineNet"] == "75.00"  # check 47: hours × the hourly rate


def test_a_rate_agreed_with_the_invoice_recipient_comes_first_on_a_copied_position(client, db_session, betrieb):
    insurer = make_contact(db_session, betrieb, name="Helvetia Versicherungen AG")
    rates.create_agreement(
        db_session, dealership_id=betrieb.id, contact=rates.AgreementContact(id=insurer.id, label="Helvetia"),
        category_id=betrieb.category_id, kind=LabourRateAgreementKind.RATE, valid_from=rates.today(),
        amount=Decimal("120.00"), entered=PriceEntry.NET, actor_id=None, on=rates.today(),
    )
    db_session.commit()
    package = _created(client, betrieb)
    order = add_job(client, betrieb, new_order(client, db_session, betrieb,
                                               invoiceRecipientContactId=str(insurer.id)))

    response = _put_on_job(client, betrieb, order, order["jobs"][0]["id"], package["id"])

    labour = response.json()["jobs"][0]["positions"][0]
    assert labour["rateSource"] == "agreement" and labour["lineNet"] == "180.00"  # 1.5 h × CHF 120.00


def test_no_rate_for_a_category_of_the_package_refuses_the_whole_copy(client, db_session):
    betrieb = make_betrieb(client, db_session, rate=None)
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)

    response = _put_on_job(client, betrieb, order, job_id, package["id"])

    assert response.status_code == 409 and _reason(response) == "service_package_labour_rate_missing"
    assert db_session.scalar(select(ServiceOrderPosition.id)) is None  # nothing landed, the text neither


def test_a_frozen_order_takes_no_package(client, db_session, betrieb):
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)
    cancelled = client.post(
        f"/v1/service-orders/{order['id']}/cancel", json={"reason": "Kunde hat abgesagt"},
        headers=headers(betrieb, if_match=order["version"]),
    )
    assert cancelled.status_code == 200, cancelled.text

    response = _put_on_job(client, betrieb, cancelled.json(), job_id, package["id"])

    assert response.status_code == 409 and _reason(response) == "service_order_frozen"


def test_only_a_writer_of_service_orders_puts_a_package_on_a_job(client, db_session, betrieb):
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)

    response = client.post(
        f"/v1/service-orders/{order['id']}/jobs/{job_id}/service-packages",
        json={"servicePackageId": package["id"]},
        headers=headers(betrieb, AccessRole.TECHNICIAN, if_match=order["version"]),
    )

    assert response.status_code == 403


def test_another_betriebs_package_is_not_found_on_a_job(client, db_session, betrieb):
    other = make_betrieb(client, db_session)
    theirs = _created(client, other)
    order, job_id = _order_with_job(client, db_session, betrieb)

    response = _put_on_job(client, betrieb, order, job_id, theirs["id"])

    assert response.status_code == 404


# --- check 45: copied, never linked --------------------------------------------------------------


def test_check_45_changing_or_deactivating_a_package_changes_no_order_and_an_inactive_one_is_not_offered(
    client, db_session, betrieb
):
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)
    copied = _put_on_job(client, betrieb, order, job_id, package["id"]).json()
    before = copied["jobs"][0]["positions"]

    changed = _put(client, betrieb, package, {
        **_put_back(package, name="Service neu"),
        "positions": [_labour(betrieb, "3.00", text="Anders"), {"kind": "text", "text": "Neu"}],
    })
    assert changed.status_code == 200, changed.text
    deactivated = _put(client, betrieb, changed.json(), _put_back(changed.json(), isActive=False))
    assert deactivated.status_code == 200 and deactivated.json()["isActive"] is False

    now = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    assert now["jobs"][0]["positions"] == before
    offered = client.get(_base(betrieb), params={"active": "true"}, headers=headers(betrieb)).json()["items"]
    assert offered == []
    every = client.get(_base(betrieb), headers=headers(betrieb)).json()["items"]
    assert [p["id"] for p in every] == [package["id"]]
    refused = _put_on_job(client, betrieb, now, job_id, package["id"])
    assert refused.status_code == 409 and _reason(refused) == "service_package_inactive"
    audit = db_session.scalars(
        select(AuditEvent.action).where(AuditEvent.entity_type == "service_package").order_by(AuditEvent.created_at)
    ).all()
    assert audit == ["create", "update", "deactivate"]


def test_a_copied_position_is_a_position_like_a_typed_one(client, db_session, betrieb):
    package = _created(client, betrieb)
    order, job_id = _order_with_job(client, db_session, betrieb)
    copied = _put_on_job(client, betrieb, order, job_id, package["id"]).json()
    labour = copied["jobs"][0]["positions"][0]

    changed = client.patch(
        f"/v1/service-orders/{order['id']}/positions/{labour['id']}", json={"time": "2.00"},
        headers=headers(betrieb, if_match=copied["version"]),
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["jobs"][0]["positions"][0]["lineNet"] == "300.00"
    assert db_session.scalars(
        select(ServicePackagePosition.time_quantity).where(ServicePackagePosition.time_quantity.is_not(None))
    ).all() == [Decimal("1.50")]


# --- criterion 7: copied from a sister Betrieb; Nexotec ships none -------------------------------


@contextmanager
def _session(engine):
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _category(engine, dealership: str, name: str) -> uuid.UUID:
    with _session(engine) as db:
        set_setting(db, uuid.UUID(dealership), RATES_ENTERED_KEY, "net")
        category = rates.create_category(db, dealership_id=uuid.UUID(dealership), name=name, name_translations=None,
                                         actor_id=None)
        db.commit()
        return category.id


def _seed_package(engine, dealership: str, name: str, category_id: uuid.UUID, *, time: str = "1.50",
                  active: bool = True) -> None:
    with _session(engine) as db:
        catalogue.create_package(
            db, dealership_id=uuid.UUID(dealership), actor_id=None,
            data=catalogue.PackageInput(
                name=name, is_active=active,
                positions=[catalogue.PackagePositionInput(kind=catalogue.PackagePositionKind.LABOUR, text="Arbeit",
                                                          labour_category_id=category_id, time=Decimal(time),
                                                          check_needed=True)],
            ),
        )


def _names(engine, dealership: str) -> dict[str, tuple[str, bool]]:
    with _session(engine) as db:
        packages = catalogue.list_packages(db, dealership_id=uuid.UUID(dealership))
        positions = catalogue.positions_of(db, [p.id for p in packages])
        return {p.name: (str(positions[p.id][0].time_quantity), positions[p.id][0].check_needed) for p in packages}


def test_criterion_7_the_catalogue_is_copied_from_a_sister_betrieb(client, engine):
    source, target, _, in_target, _ = _pair(client, engine)
    _entitle(engine, source, target, modules=(ModuleCode.AFTERSALES,))
    in_source_category = _category(engine, source, "Mechanik")
    _category(engine, target, "mechanik")  # matched by name, case ignored
    _seed_package(engine, source, "Service 30'000 km", in_source_category)
    _seed_package(engine, source, "Klimaservice", in_source_category, time="0.75")
    _seed_package(engine, source, "Alt", in_source_category, active=False)  # inactive: not copied
    elsewhere = _category(engine, source, "Karosserie")  # the target has no such category
    _seed_package(engine, source, "Delle", elsewhere)
    target_category = _category(engine, target, "Elektrik")
    _seed_package(engine, target, "Klimaservice", target_category, time="2.00")  # overwritten
    _seed_package(engine, target, "Eigenes", target_category)  # the source lacks it: kept

    preview = _copy(client, target, in_target, source, dry_run=True, subsets=("service_catalogue",))
    assert preview.status_code == 200, preview.text
    assert _names(engine, target) == {"Klimaservice": ("2.00", True), "Eigenes": ("1.50", True)}  # nothing written

    done = _copy(client, target, in_target, source, dry_run=False, subsets=("service_catalogue",))

    assert done.status_code == 200, done.text
    items = {item["key"]: (item["outcome"], item["reason"]) for item in done.json()["items"]}
    assert items == {
        "Service 30'000 km": ("created", None),
        "Klimaservice": ("overwritten", None),
        "Delle": ("skipped", "labour_category_missing"),
    }
    assert {item["key"]: item["outcome"] for item in preview.json()["items"]} == {k: v[0] for k, v in items.items()}
    assert _names(engine, target) == {
        "Service 30'000 km": ("1.50", True), "Klimaservice": ("0.75", True), "Eigenes": ("1.50", True),
    }
    again = _copy(client, target, in_target, source, dry_run=False, subsets=("service_catalogue",))
    assert {item["key"]: item["outcome"] for item in again.json()["items"]}["Klimaservice"] == "unchanged"


def test_criterion_7_a_copy_never_undoes_a_deactivation_in_the_target(client, engine):
    source, target, _, in_target, _ = _pair(client, engine)
    _entitle(engine, source, target, modules=(ModuleCode.AFTERSALES,))
    _seed_package(engine, source, "Klimaservice", _category(engine, source, "Mechanik"), time="0.75")
    _seed_package(engine, target, "Klimaservice", _category(engine, target, "Mechanik"), time="2.00", active=False)

    done = _copy(client, target, in_target, source, dry_run=False, subsets=("service_catalogue",))

    assert [(i["key"], i["outcome"], i["reason"]) for i in done.json()["items"]] == [
        ("Klimaservice", "skipped", "package_inactive_here")
    ]
    with _session(engine) as db:
        (kept,) = catalogue.list_packages(db, dealership_id=uuid.UUID(target))
        assert kept.is_active is False and kept.version == 1


def test_criterion_7_without_aftersales_in_both_the_catalogue_is_one_skipped_item(client, engine):
    source, target, _, in_target, _ = _pair(client, engine)
    _entitle(engine, source, modules=(ModuleCode.AFTERSALES,))

    done = _copy(client, target, in_target, source, dry_run=False, subsets=("service_catalogue",))

    assert done.status_code == 200, done.text
    assert [(i["key"], i["outcome"], i["reason"]) for i in done.json()["items"]] == [
        ("service_catalogue", "skipped", "module_not_held")
    ]


def test_criterion_7_nexotec_ships_no_package(client, db_session, betrieb):
    assert client.get(_base(betrieb), headers=_manager(betrieb)).json()["items"] == []


# --- rule 2 and rule 10: the label refresh and its gauge ----------------------------------------


def test_the_nightly_refresh_takes_a_renamed_categorys_name_and_the_gauge_reads_its_age(client, db_session, betrieb):
    package = _created(client, betrieb)
    rates.change_category(db_session, dealership_id=betrieb.id, category_id=betrieb.category_id,
                          expected_version=1, actor_id=None, name="Mechanik & Service")
    db_session.commit()

    report = catalogue.refresh_package_labels(db_session)

    assert len(report.changed) == 1 and report.unresolved == []
    db_session.expire_all()
    row = db_session.scalar(select(ServicePackagePosition).where(ServicePackagePosition.labour_category_id.is_not(None)))
    assert row.labour_category_label == "Mechanik & Service"
    assert db_session.get(ServicePackage, uuid.UUID(package["id"])).version == 1  # a copy refreshed, no version
    age = catalogue.oldest_package_label_age_seconds(db_session)
    assert age is not None and age < 60

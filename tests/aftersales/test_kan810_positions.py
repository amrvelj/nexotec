"""KAN-810 (AS1-07): jobs and positions on the workshop order — labour at a
fixed price, labour by time, text (table 3.3), the rate and its source
(check 11), the typed price net or gross (check 12), hours or work units
(checks 47, 48), and the total in two parts, accepted and still open
(exit criterion 4)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJob
from app.aftersales.services import positions as positions_service
from app.aftersales.services import work_guard
from app.core.money import PriceEntry
from app.platform.organisation.models.labour_rate import LabourRateAgreementKind
from app.platform.organisation.services import labour_rates as rates
from app.platform.organisation.services.labour_rate_settings import TIME_UNIT_KEY, WORK_UNITS_PER_HOUR_KEY
from tests.aftersales.service_order_support import (
    add_job,
    add_position,
    create_order,
    headers,
    labour_by_time,
    make_betrieb,
    make_contact,
    make_vehicle,
    new_order,
    set_setting,
    work_units,
)


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _with_job(client, db_session, betrieb, **order_extra):
    order = new_order(client, db_session, betrieb, **order_extra)
    order = add_job(client, betrieb, order)
    return order, order["jobs"][0]["id"]


def _only_position(order: dict) -> dict:
    return order["jobs"][0]["positions"][-1]


# --- check 11: the rate «which applies here», with its source --------------------------------------


def test_check_11_labour_by_time_takes_the_category_rate_and_names_its_source(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)

    response = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1.50"))

    assert response.status_code == 201, response.text
    position = _only_position(response.json())
    assert position["rateSource"] == "category_rate"
    assert position["labourRateId"] is not None
    assert position["labourCategoryLabel"] == "Mechanik"
    assert position["unitPrice"]["amount"] == "150.0000" and position["unitPrice"]["entered"] == "net"
    assert position["lineNet"] == "225.00"  # 1.5 h × CHF 150.00


def test_check_11_a_rate_agreed_with_the_invoice_recipient_comes_first(client, db_session, betrieb):
    insurer = make_contact(db_session, betrieb, name="Helvetia Versicherungen AG")
    rates.create_agreement(
        db_session, dealership_id=betrieb.id, contact=rates.AgreementContact(id=insurer.id, label="Helvetia"),
        category_id=betrieb.category_id, kind=LabourRateAgreementKind.RATE, valid_from=rates.today(),
        amount=Decimal("120.00"), entered=PriceEntry.NET, actor_id=None, on=rates.today(),
    )
    db_session.commit()
    order, job_id = _with_job(client, db_session, betrieb, invoiceRecipientContactId=str(insurer.id))

    proposal = client.get(
        f"/v1/service-orders/{order['id']}/labour-rate-proposal?labourCategoryId={betrieb.category_id}",
        headers=headers(betrieb),
    ).json()
    assert (proposal["found"], proposal["source"], proposal["unitPrice"]["amount"]) == (True, "agreement", "120.0000")
    assert proposal["timeUnit"] == "hours"

    position = _only_position(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1")).json())
    assert (position["rateSource"], position["unitPrice"]["amount"], position["lineNet"]) == (
        "agreement", "120.0000", "120.00",
    )


def test_without_a_rate_the_price_is_typed(client, db_session):
    betrieb = make_betrieb(client, db_session, rate=None)
    order, job_id = _with_job(client, db_session, betrieb)

    missing = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1"))
    assert (missing.status_code, _reason(missing)) == (409, "service_order_labour_rate_missing")
    typed = add_position(client, betrieb, order, job_id,
                         **labour_by_time(betrieb, "1", unitPrice="140.00", priceEntered="net"))
    assert typed.status_code == 201, typed.text
    assert _only_position(typed.json())["rateSource"] == "typed"


def test_a_labour_category_of_another_betrieb_is_refused(client, db_session, betrieb):
    other = make_betrieb(client, db_session)
    order, job_id = _with_job(client, db_session, betrieb)
    body = labour_by_time(betrieb, "1") | {"labourCategoryId": str(other.category_id)}
    response = add_position(client, betrieb, order, job_id, **body)
    assert (response.status_code, _reason(response)) == (422, "service_order_labour_category_unknown")


# --- check 12: the typed price leads --------------------------------------------------------------


def test_check_12_typed_net_the_gross_follows_and_typed_gross_the_net_follows(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)

    net = add_position(client, betrieb, order, job_id, kind="labour_fixed", text="Pauschale",
                       labourCategoryId=str(betrieb.category_id), unitPrice="100.00", priceEntered="net")
    assert net.status_code == 201, net.text
    price = _only_position(net.json())["unitPrice"]
    assert (price["amount"], price["entered"], price["net"], price["gross"]) == ("100.0000", "net", "100.0000", "108.1000")

    order = net.json()
    gross = add_position(client, betrieb, order, job_id, kind="labour_fixed", text="Pauschale brutto",
                         labourCategoryId=str(betrieb.category_id), unitPrice="19.95", priceEntered="gross")
    price = _only_position(gross.json())["unitPrice"]
    assert (price["amount"], price["entered"], price["gross"], price["net"]) == ("19.9500", "gross", "19.9500", "18.4551")
    assert _only_position(gross.json())["lineGross"] == "19.95"


def test_check_12_a_rate_typed_with_vat_is_read_with_vat(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="162.15", entered=PriceEntry.GROSS)
    order, job_id = _with_job(client, db_session, betrieb)

    position = _only_position(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1")).json())

    assert (position["unitPrice"]["entered"], position["unitPrice"]["gross"], position["unitPrice"]["net"]) == (
        "gross", "162.1500", "150.0000",
    )
    assert (position["lineGross"], position["lineNet"]) == ("162.15", "150.00")


def test_a_price_has_at_most_four_decimals(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    response = add_position(client, betrieb, order, job_id, kind="labour_fixed", text="x",
                            labourCategoryId=str(betrieb.category_id), unitPrice="1.00001", priceEntered="net")
    assert (response.status_code, _reason(response)) == (422, "service_order_price_too_many_decimals")


def test_no_price_without_the_betriebs_vat_rate(client, db_session):
    betrieb = make_betrieb(client, db_session, vat_rate=None)
    order, job_id = _with_job(client, db_session, betrieb)
    response = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1"))
    assert (response.status_code, _reason(response)) == (409, "service_order_vat_rate_not_set")
    # A text needs no price.
    assert add_position(client, betrieb, order, job_id, kind="text", text="Hinweis").status_code == 201


@pytest.mark.parametrize(
    "body, field",
    [
        ({"kind": "labour_time", "text": "x"}, "labourCategoryId"),
        ({"kind": "labour_fixed", "text": "x", "labourCategoryId": "CAT"}, "unitPrice"),
        ({"kind": "labour_time", "text": "x", "labourCategoryId": "CAT"}, "time"),
        ({"kind": "labour_fixed", "text": "x", "labourCategoryId": "CAT", "unitPrice": "10"}, "priceEntered"),
        ({"kind": "text", "text": "x", "unitPrice": "10", "priceEntered": "net"}, "kind"),
        ({"kind": "text", "text": "  "}, "text"),
    ],
)
def test_each_kind_holds_what_it_needs(client, db_session, betrieb, body, field):
    order, job_id = _with_job(client, db_session, betrieb)
    if body.get("labourCategoryId") == "CAT":
        body = body | {"labourCategoryId": str(betrieb.category_id)}
    response = add_position(client, betrieb, order, job_id, **body)
    assert (response.status_code, _reason(response), response.json()["error"]["details"]["field"]) == (
        422, "service_order_position_incomplete", field,
    )


# --- checks 47 and 48: hours or work units ----------------------------------------------------------


def test_check_47_nine_work_units_at_twelve_per_hour_and_chf_100_are_chf_75(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="100.00")
    work_units(db_session, betrieb, 12)
    order, job_id = _with_job(client, db_session, betrieb)

    position = _only_position(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "9")).json())

    assert position["time"] == {"quantity": "9.00", "unit": "work_units", "workUnitsPerHour": "12.00", "hours": "0.7500"}
    assert position["lineNet"] == "75.00"
    assert position["unitPrice"]["amount"] == "100.0000"  # the rate per hour; never per work unit


def test_check_47_the_same_position_in_a_betrieb_on_hours_shows_hours(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="100.00")
    order, job_id = _with_job(client, db_session, betrieb)

    position = _only_position(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "0.75")).json())

    assert position["time"] == {"quantity": "0.75", "unit": "hours", "workUnitsPerHour": None, "hours": "0.7500"}
    assert position["lineNet"] == "75.00"


def test_check_48_changing_the_unit_or_the_work_units_per_hour_changes_no_position(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="100.00")
    work_units(db_session, betrieb, 12)
    order, job_id = _with_job(client, db_session, betrieb)
    before = _only_position(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "9")).json())

    set_setting(db_session, betrieb.id, WORK_UNITS_PER_HOUR_KEY, Decimal(10))
    after_per_hour = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    set_setting(db_session, betrieb.id, TIME_UNIT_KEY, "hours")
    after_unit = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()

    for order_now in (after_per_hour, after_unit):
        position = _only_position(order_now)
        assert position["time"] == before["time"]
        assert position["lineNet"] == "75.00"
    # A change of the text alone keeps the unit it was typed in.
    changed = client.patch(
        f"/v1/service-orders/{order['id']}/positions/{before['id']}", json={"text": "Bremsen prüfen"},
        headers=headers(betrieb, if_match=after_unit["version"]),
    )
    assert changed.status_code == 200, changed.text
    assert _only_position(changed.json())["time"] == before["time"]


def test_a_time_typed_in_a_unit_no_longer_in_force_is_refused(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="100.00")
    work_units(db_session, betrieb, 12)
    order, job_id = _with_job(client, db_session, betrieb)
    response = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "9", timeUnit="hours"))
    assert (response.status_code, _reason(response)) == (409, "service_order_time_unit_changed")
    assert response.json()["error"]["details"]["timeUnit"] == "work_units"


@pytest.mark.parametrize("time, reason", [("0", "service_order_time_not_positive"),
                                          ("1.255", "service_order_time_too_many_decimals")])
def test_a_labour_time_is_above_zero_with_two_decimals(client, db_session, betrieb, time, reason):
    order, job_id = _with_job(client, db_session, betrieb)
    response = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, time))
    assert (response.status_code, _reason(response)) == (422, reason)


# --- exit criterion 4: the total in two parts -------------------------------------------------------


def test_the_total_has_an_accepted_and_an_open_part(client, db_session):
    betrieb = make_betrieb(client, db_session, rate="434.85")
    contact = make_contact(db_session, betrieb)
    order = create_order(client, betrieb, contact.id, make_vehicle(db_session)).json()
    order = add_job(client, betrieb, order, "Service")
    order = add_job(client, betrieb, order, "Bremsen")
    accepted_job, open_job = (job["id"] for job in order["jobs"])
    order = add_position(client, betrieb, order, accepted_job, **labour_by_time(betrieb, "1")).json()
    order = add_position(client, betrieb, order, open_job, **labour_by_time(betrieb, "2")).json()
    order = add_position(client, betrieb, order, open_job, kind="text", text="Hinweis").json()

    row = db_session.get(ServiceOrder, uuid.UUID(order["id"]))
    job = db_session.get(ServiceOrderJob, uuid.UUID(accepted_job))
    positions_service.accept_job(db_session, order=row, job=job, actor_id=betrieb.user_id)

    body = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    assert body["state"] == "accepted"  # the first accepted job makes the order accepted (table 3.1)
    (accepted,), (still_open,) = body["totals"]["accepted"], body["totals"]["open"]
    # The example of Part A (check 13): net 434.85, VAT 35.22, 470.07, rounding −0.02, total 470.05.
    assert (accepted["payer"], accepted["net"], accepted["vat"], accepted["total"]) == ("customer", "434.85", "35.22", "470.07")
    assert (accepted["rounding"], accepted["roundedTotal"]) == ("-0.02", "470.05")
    assert (still_open["net"], still_open["total"]) == ("869.70", "940.15")


def test_a_declined_job_counts_in_neither_part(db_session):
    from app.aftersales.models.service_order import BillingCategory, JobAnswerState, PositionKind, ServiceOrderPosition
    from app.aftersales.services.order_money import order_totals

    job = ServiceOrderJob(id=uuid.uuid4(), answer_state=JobAnswerState.DECLINED, billing_category=BillingCategory.CUSTOMER)
    position = ServiceOrderPosition(job_id=job.id, kind=PositionKind.LABOUR_FIXED, unit_price=Decimal(100),
                                    price_entered=PriceEntry.NET, vat_rate=Decimal("8.1"))
    totals = order_totals([job], [position], cash_rounding=Decimal("0.05"))
    assert (totals.accepted, totals.open) == ((), ())


# --- table 3.3 and 3.2: change and delete -----------------------------------------------------------


def test_a_typed_price_cleared_takes_the_rate_again(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id,
                         **labour_by_time(betrieb, "1", unitPrice="99.00", priceEntered="gross")).json()
    position = _only_position(order)
    assert position["rateSource"] == "typed"

    changed = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}",
                           json={"unitPrice": None}, headers=headers(betrieb, if_match=order["version"]))
    assert changed.status_code == 200, changed.text
    assert (_only_position(changed.json())["rateSource"], _only_position(changed.json())["lineNet"]) == (
        "category_rate", "150.00",
    )


def test_a_position_is_deleted_unless_time_was_clocked_on_it(client, db_session, betrieb, monkeypatch):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id, kind="text", text="Hinweis").json()
    position_id = _only_position(order)["id"]
    url = f"/v1/service-orders/{order['id']}/positions/{position_id}"

    monkeypatch.setattr(work_guard, "time_clocked_on_position", lambda db, *, tenant_id, position_id: True)
    refused = client.delete(url, headers=headers(betrieb, if_match=order["version"]))
    assert (refused.status_code, _reason(refused)) == (409, "service_order_position_time_clocked")
    monkeypatch.undo()
    deleted = client.delete(url, headers=headers(betrieb, if_match=order["version"]))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["jobs"][0]["positions"] == []


def test_an_accepted_job_is_not_deleted(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    row = db_session.get(ServiceOrder, uuid.UUID(order["id"]))
    positions_service.accept_job(
        db_session, order=row, job=db_session.get(ServiceOrderJob, uuid.UUID(job_id)), actor_id=betrieb.user_id
    )
    response = client.delete(f"/v1/service-orders/{order['id']}/jobs/{job_id}",
                             headers=headers(betrieb, if_match=row.version))
    assert (response.status_code, _reason(response)) == (409, "service_order_job_accepted")


def test_a_draft_job_is_deleted_with_its_positions(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id, kind="text", text="Hinweis").json()
    response = client.delete(f"/v1/service-orders/{order['id']}/jobs/{job_id}",
                             headers=headers(betrieb, if_match=order["version"]))
    assert response.status_code == 200, response.text
    assert response.json()["jobs"] == []
    assert db_session.scalar(select(ServiceOrderJob.id)) is None


def test_a_job_has_the_billing_category_customer(client, db_session, betrieb):
    order, _ = _with_job(client, db_session, betrieb)
    assert order["jobs"][0]["billingCategory"] == "customer"
    assert order["jobs"][0]["answerState"] == "draft"


# --- a change keeps the rate it was priced with (ruling 178, section 3.2) ----------------------------


def test_a_time_change_keeps_the_proposed_rate_after_the_invoice_recipient_changed(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1")).json()
    position = _only_position(order)
    assert (position["rateSource"], position["unitPrice"]["amount"]) == ("category_rate", "150.0000")
    insurer = make_contact(db_session, betrieb, name="Helvetia Versicherungen AG")
    rates.create_agreement(
        db_session, dealership_id=betrieb.id, contact=rates.AgreementContact(id=insurer.id, label="Helvetia"),
        category_id=betrieb.category_id, kind=LabourRateAgreementKind.RATE, valid_from=rates.today(),
        amount=Decimal("120.00"), entered=PriceEntry.NET, actor_id=None, on=rates.today(),
    )
    db_session.commit()
    order = client.patch(f"/v1/service-orders/{order['id']}", json={"invoiceRecipientContactId": str(insurer.id)},
                         headers=headers(betrieb, if_match=order["version"])).json()

    changed = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}", json={"time": "2"},
                           headers=headers(betrieb, if_match=order["version"]))

    assert changed.status_code == 200, changed.text
    after = _only_position(changed.json())
    assert (after["rateSource"], after["labourRateId"], after["unitPrice"]["amount"]) == (
        "category_rate", position["labourRateId"], "150.0000",
    )
    assert after["lineNet"] == "300.00"  # the new hours × the kept rate
    assert (after["pricedAt"], after["pricedBy"]) == (position["pricedAt"], position["pricedBy"])


def test_a_time_change_on_a_typed_price_keeps_who_priced_it_and_when(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id,
                         **labour_by_time(betrieb, "1", unitPrice="99.00", priceEntered="net")).json()
    position = _only_position(order)
    colleague = headers(betrieb, if_match=order["version"], user_id=uuid.uuid4())

    changed = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}", json={"time": "1.5"},
                           headers=colleague)

    assert changed.status_code == 200, changed.text
    after = _only_position(changed.json())
    assert (after["rateSource"], after["unitPrice"]["amount"], after["lineNet"]) == ("typed", "99.0000", "148.50")
    assert (after["pricedAt"], after["pricedBy"]) == (position["pricedAt"], position["pricedBy"])
    # A new price is a new «who priced it».
    repriced = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}",
                            json={"unitPrice": "110.00", "priceEntered": "net"},
                            headers=headers(betrieb, if_match=changed.json()["version"], user_id=uuid.uuid4()))
    assert repriced.status_code == 200, repriced.text
    assert _only_position(repriced.json())["pricedBy"] != position["pricedBy"]


@pytest.mark.parametrize(
    "body",
    [
        {"labourCategoryId": "CAT"},
        {"time": "1"},
        {"unitPrice": "10.00", "priceEntered": "net"},
        {"priceEntered": "net"},
    ],
)
def test_a_change_to_a_text_position_sends_only_what_a_text_holds(client, db_session, betrieb, body):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id, kind="text", text="Hinweis").json()
    position = _only_position(order)
    if body.get("labourCategoryId") == "CAT":
        body = body | {"labourCategoryId": str(betrieb.category_id)}

    response = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}", json=body,
                            headers=headers(betrieb, if_match=order["version"]))

    assert (response.status_code, _reason(response), response.json()["error"]["details"]["field"]) == (
        422, "service_order_position_incomplete", "kind",
    )


def test_a_change_sends_net_or_gross_only_with_the_price(client, db_session, betrieb):
    order, job_id = _with_job(client, db_session, betrieb)
    order = add_position(client, betrieb, order, job_id,
                         **labour_by_time(betrieb, "1", unitPrice="99.00", priceEntered="net")).json()
    position = _only_position(order)

    response = client.patch(f"/v1/service-orders/{order['id']}/positions/{position['id']}",
                            json={"priceEntered": "gross"}, headers=headers(betrieb, if_match=order["version"]))

    assert (response.status_code, _reason(response), response.json()["error"]["details"]["field"]) == (
        422, "service_order_position_incomplete", "unitPrice",
    )
    unchanged = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    assert _only_position(unchanged)["unitPrice"]["entered"] == "net"

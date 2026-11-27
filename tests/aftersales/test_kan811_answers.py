"""KAN-811 (AS1-08): the customer's answer per job — build slice 1, table 3.2;
checks 3, 4, 5 end to end and the rule parts of 6, 7, 8 (the clock is
KAN-814's: `work_guard` is patched where a rule asks it)."""

import uuid

import pytest
from sqlalchemy import select

from app.aftersales.models.service_order import ServiceOrder, ServiceOrderJobAnswer
from app.aftersales.services import work_guard
from tests.aftersales.service_order_support import (
    add_job,
    add_position,
    headers,
    labour_by_time,
    make_betrieb,
    new_order,
)


@pytest.fixture()
def betrieb(client, db_session):
    return make_betrieb(client, db_session)


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


def _answer(client, betrieb, order: dict, *entries: dict, channel: str = "phone"):
    return client.post(
        f"/v1/service-orders/{order['id']}/answers", json={"channel": channel, "jobs": list(entries)},
        headers=headers(betrieb, if_match=order["version"]),
    )


def _ok(response) -> dict:
    assert response.status_code in (200, 201), response.text
    return response.json()


def _job(order: dict, job_id: str) -> dict:
    return next(job for job in order["jobs"] if job["id"] == job_id)


def _priced_job(client, db_session, betrieb, time: str = "1") -> tuple[dict, str]:
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    job_id = order["jobs"][0]["id"]
    order = _ok(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, time)))
    return order, job_id


def _accepted(client, db_session, betrieb) -> tuple[dict, str]:
    order, job_id = _priced_job(client, db_session, betrieb)
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}))
    return order, job_id


# --- check 4: the first accepted job makes the order accepted; who, how, when -----------------------


def test_check_4_the_first_accepted_job_accepts_the_order_and_the_answer_says_who_how_when(
    client, db_session, betrieb
):
    order, job_id = _priced_job(client, db_session, betrieb)
    order = add_job(client, betrieb, order, "Bremsen")
    assert order["state"] == "draft"

    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}, channel="phone"))

    assert order["state"] == "accepted"
    job = _job(order, job_id)
    assert job["answerState"] == "accepted"
    (answer,) = job["answers"]
    assert (answer["result"], answer["channel"]) == ("accepted", "phone")
    assert answer["recordedBy"] == str(betrieb.user_id)
    assert answer["recordedByLabel"] == "Sandra Kundendienst"
    assert answer["recordedAt"]
    # The amount said yes to: 1 h at CHF 150.00 net, VAT 8.1 %.
    assert answer["amount"] == {"net": "150.00", "gross": "162.15"}
    # The other job still has no answer.
    assert _job(order, order["jobs"][1]["id"])["answerState"] == "draft"


def test_the_answer_is_in_the_history(client, db_session, betrieb):
    order, _job_id = _accepted(client, db_session, betrieb)
    items = client.get(f"/v1/service-orders/{order['id']}/history", headers=headers(betrieb)).json()["items"]
    answered = [item for item in items if item["action"] == "service_order_job_answered"]
    assert len(answered) == 1
    assert (answered[0]["after"]["result"], answered[0]["after"]["orderState"]) == ("accepted", "accepted")


# --- check 3: a yes before a price; positions entered later need no new answer -----------------------


def test_check_3_a_job_accepted_without_a_price_keeps_its_yes_however_many_positions_follow(
    client, db_session, betrieb
):
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    job_id = order["jobs"][0]["id"]
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}, channel="desk"))
    assert _job(order, job_id)["answers"][0]["amount"] is None  # ruling 167: no price yet

    order = _ok(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1")))
    order = _ok(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "2.5")))
    position_id = _job(order, job_id)["positions"][0]["id"]
    order = _ok(client.patch(f"/v1/service-orders/{order['id']}/positions/{position_id}",
                             json={"unitPrice": "180.00", "priceEntered": "net"},
                             headers=headers(betrieb, if_match=order["version"])))

    job = _job(order, job_id)
    assert job["answerState"] == "accepted"
    assert job["acceptedBefore"] is None
    assert len(job["answers"]) == 1
    # The order says who entered each price and when.
    for position in job["positions"]:
        assert position["pricedByLabel"] == "Sandra Kundendienst"
        assert position["pricedAt"]


# --- check 5: the reset when the amount changes; a text alone changes nothing ------------------------


def test_check_5_a_changed_amount_makes_the_job_wait_and_keeps_accepted_before(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    position_id = _job(order, job_id)["positions"][0]["id"]

    order = _ok(client.patch(f"/v1/service-orders/{order['id']}/positions/{position_id}", json={"time": "2"},
                             headers=headers(betrieb, if_match=order["version"])))

    job = _job(order, job_id)
    assert job["answerState"] == "waiting"
    assert job["acceptedBefore"] == {"net": "150.00", "gross": "162.15"}
    assert job["amount"] == {"net": "300.00", "gross": "324.30"}
    assert order["waitingForAnswer"] is True

    # Accepted again: the new amount is the one said yes to; «accepted before» is gone.
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}))
    job = _job(order, job_id)
    assert (job["answerState"], job["acceptedBefore"]) == ("accepted", None)
    assert job["answers"][-1]["amount"] == {"net": "300.00", "gross": "324.30"}


def test_check_5_correcting_the_text_changes_nothing(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    position_id = _job(order, job_id)["positions"][0]["id"]

    order = _ok(client.patch(f"/v1/service-orders/{order['id']}/positions/{position_id}",
                             json={"text": "Arbeit, korrigiert"}, headers=headers(betrieb, if_match=order["version"])))
    order = _ok(add_position(client, betrieb, order, job_id, kind="text", text="Hinweis"))

    assert _job(order, job_id)["answerState"] == "accepted"


def test_check_5_adding_or_deleting_a_priced_position_resets_too(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    order = _ok(add_position(client, betrieb, order, job_id, **labour_by_time(betrieb, "1")))
    assert _job(order, job_id)["answerState"] == "waiting"

    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}))
    position_id = _job(order, job_id)["positions"][-1]["id"]
    order = _ok(client.delete(f"/v1/service-orders/{order['id']}/positions/{position_id}",
                              headers=headers(betrieb, if_match=order["version"])))
    assert _job(order, job_id)["answerState"] == "waiting"
    assert _job(order, job_id)["acceptedBefore"] == {"net": "300.00", "gross": "324.30"}


# --- declined; the refusals of table 3.2 ---------------------------------------------------------------


def test_a_declined_job_needs_a_note(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    refused = _answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "  "})
    assert (refused.status_code, _reason(refused)) == (422, "service_order_answer_note_required")

    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "Zu teuer"}))
    job = _job(order, job_id)
    assert (job["answerState"], job["answers"][0]["note"], job["stopped"]) == ("declined", "Zu teuer", False)
    assert order["state"] == "draft"  # a declined job accepts nothing


@pytest.mark.parametrize("result", ["accepted", "declined"])
def test_a_declined_job_has_its_answer(client, db_session, betrieb, result):
    order, job_id = _priced_job(client, db_session, betrieb)
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "Nein"}))
    refused = _answer(client, betrieb, order, {"jobId": job_id, "result": result, "note": "x"})
    assert (refused.status_code, _reason(refused)) == (409, "service_order_job_already_answered")


def test_an_accepted_job_is_not_accepted_twice(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    refused = _answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"})
    assert (refused.status_code, _reason(refused)) == (409, "service_order_job_already_answered")


def test_an_accepted_job_without_clocked_time_is_declined_without_stopping(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "Doch nicht"}))
    job = _job(order, job_id)
    assert (job["answerState"], job["stopped"], job["hoursBilled"]) == ("declined", False, None)


def test_answers_are_whole_or_not_at_all(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    order = add_job(client, betrieb, order, "Bremsen")
    other = order["jobs"][1]["id"]
    refused = _answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"},
                      {"jobId": other, "result": "declined"})
    assert (refused.status_code, _reason(refused)) == (422, "service_order_answer_note_required")
    assert db_session.scalar(select(ServiceOrderJobAnswer.id)) is None
    body = client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json()
    assert (body["state"], _job(body, job_id)["answerState"]) == ("draft", "draft")


def test_an_answer_names_at_least_one_job_once(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    empty = _answer(client, betrieb, order)
    assert (empty.status_code, _reason(empty)) == (422, "service_order_answer_empty")
    twice = _answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"},
                    {"jobId": job_id, "result": "accepted"})
    assert (twice.status_code, _reason(twice)) == (422, "service_order_answer_job_twice")
    unknown = _answer(client, betrieb, order, {"jobId": str(uuid.uuid4()), "result": "accepted"})
    assert unknown.status_code == 404


def test_an_answer_takes_the_orders_version(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    stale = client.post(f"/v1/service-orders/{order['id']}/answers",
                        json={"channel": "desk", "jobs": [{"jobId": job_id, "result": "accepted"}]},
                        headers=headers(betrieb, if_match=order["version"] - 1))
    assert stale.status_code == 409
    missing = client.post(f"/v1/service-orders/{order['id']}/answers",
                          json={"channel": "desk", "jobs": [{"jobId": job_id, "result": "accepted"}]},
                          headers=headers(betrieb))
    assert missing.status_code == 400


def test_a_cancelled_order_takes_no_answer(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    order = _ok(client.post(f"/v1/service-orders/{order['id']}/cancel", json={"reason": "Kunde sagt ab"},
                            headers=headers(betrieb, if_match=order["version"])))
    refused = _answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"})
    assert (refused.status_code, _reason(refused)) == (409, "service_order_frozen")


def test_another_betriebs_order_is_not_found(client, db_session, betrieb):
    order, job_id = _priced_job(client, db_session, betrieb)
    other = make_betrieb(client, db_session)
    response = client.post(f"/v1/service-orders/{order['id']}/answers",
                           json={"channel": "desk", "jobs": [{"jobId": job_id, "result": "accepted"}]},
                           headers=headers(other, if_match=order["version"]))
    assert response.status_code == 404


# --- checks 7 and 8, the rule parts: clocked time, «stopped» ----------------------------------------------


def _clocked(monkeypatch, *, stopped: list | None = None) -> None:
    monkeypatch.setattr(work_guard, "time_clocked_on_job", lambda db, *, tenant_id, job_id: True)

    def stop(db, *, tenant_id, job_id, actor_id):
        person = work_guard.ClockedPerson(user_id=uuid.uuid4(), label="Marco Mechaniker")
        if stopped is not None:
            stopped.append(job_id)
        return [person]

    monkeypatch.setattr(work_guard, "stop_clocks_on_job", stop)


def test_check_7_a_job_with_clocked_time_cannot_be_deleted_but_can_be_declined(client, db_session, betrieb, monkeypatch):
    order, job_id = _priced_job(client, db_session, betrieb)
    _clocked(monkeypatch)
    refused = client.delete(f"/v1/service-orders/{order['id']}/jobs/{job_id}",
                            headers=headers(betrieb, if_match=order["version"]))
    assert (refused.status_code, _reason(refused)) == (409, "service_order_job_time_clocked")

    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "Kunde will nicht"}))
    assert _job(order, job_id)["answerState"] == "declined"


@pytest.mark.parametrize(("hours_billed", "state"), [(True, "accepted"), (False, "declined")])
def test_check_8_declining_an_accepted_job_with_clocked_time_asks_hours_billed_and_stops_its_clocks(
    client, db_session, betrieb, monkeypatch, hours_billed, state
):
    order, job_id = _accepted(client, db_session, betrieb)
    stopped: list = []
    _clocked(monkeypatch, stopped=stopped)
    assert _job(client.get(f"/v1/service-orders/{order['id']}", headers=headers(betrieb)).json(), job_id)["timeClocked"]

    asked = _answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "Abbruch"})
    assert (asked.status_code, _reason(asked)) == (422, "service_order_answer_hours_billed_required")
    assert stopped == []

    order = _ok(_answer(client, betrieb, order,
                        {"jobId": job_id, "result": "declined", "note": "Abbruch", "hoursBilled": hours_billed}))
    job = _job(order, job_id)
    assert (job["answerState"], job["stopped"], job["hoursBilled"]) == (state, True, hours_billed)
    assert stopped == [uuid.UUID(job_id)]
    items = client.get(f"/v1/service-orders/{order['id']}/history", headers=headers(betrieb)).json()["items"]
    assert [i["after"]["peopleTold"] for i in items if i["action"] == "service_order_job_answered"][-1] == [
        "Marco Mechaniker"
    ]


def test_check_8_a_stopped_job_is_not_changed_or_answered_again(client, db_session, betrieb, monkeypatch):
    order, job_id = _accepted(client, db_session, betrieb)
    _clocked(monkeypatch)
    order = _ok(_answer(client, betrieb, order,
                        {"jobId": job_id, "result": "declined", "note": "Abbruch", "hoursBilled": True}))
    position_id = _job(order, job_id)["positions"][0]["id"]
    v = order["version"]

    for refused in (
        add_position(client, betrieb, order, job_id, kind="text", text="Mehr"),
        client.patch(f"/v1/service-orders/{order['id']}/positions/{position_id}", json={"text": "x"},
                     headers=headers(betrieb, if_match=v)),
        client.patch(f"/v1/service-orders/{order['id']}/jobs/{job_id}", json={"name": "x"},
                     headers=headers(betrieb, if_match=v)),
        client.post(f"/v1/service-orders/{order['id']}/jobs/{job_id}/service-packages",
                    json={"servicePackageId": str(uuid.uuid4())}, headers=headers(betrieb, if_match=v)),
        _answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}),
        _answer(client, betrieb, order, {"jobId": job_id, "result": "declined", "note": "x", "hoursBilled": False}),
    ):
        assert (refused.status_code, _reason(refused)) == (409, "service_order_job_stopped"), refused.text


# --- check 6, the rule part: a change while somebody is clocked asks first --------------------------------


def test_check_6_changing_an_accepted_job_while_a_clock_runs_asks_first_and_names_the_person(
    client, db_session, betrieb, monkeypatch
):
    order, job_id = _accepted(client, db_session, betrieb)
    person = work_guard.ClockedPerson(user_id=uuid.uuid4(), label="Marco Mechaniker")
    monkeypatch.setattr(work_guard, "people_clocked_on_job", lambda db, *, tenant_id, job_id: [person])
    position_id = _job(order, job_id)["positions"][0]["id"]
    url = f"/v1/service-orders/{order['id']}/positions/{position_id}"

    asked = client.patch(url, json={"time": "2"}, headers=headers(betrieb, if_match=order["version"]))
    assert (asked.status_code, _reason(asked)) == (409, "service_order_job_clocked_confirm")
    assert asked.json()["error"]["details"]["people"] == [{"userId": str(person.user_id), "label": "Marco Mechaniker"}]
    assert "Marco Mechaniker" in asked.json()["error"]["message"]

    order = _ok(client.patch(url, json={"time": "2", "confirmWhileClocked": True},
                             headers=headers(betrieb, if_match=order["version"])))
    job = _job(order, job_id)
    assert (job["answerState"], job["workWithoutAnswer"], order["workWithoutAnswer"]) == ("waiting", True, True)

    # Recorded, the work has its answer again.
    order = _ok(_answer(client, betrieb, order, {"jobId": job_id, "result": "accepted"}))
    assert (_job(order, job_id)["workWithoutAnswer"], order["workWithoutAnswer"]) == (False, False)


def test_check_6_a_draft_job_is_changed_without_asking(client, db_session, betrieb, monkeypatch):
    order, job_id = _priced_job(client, db_session, betrieb)
    person = work_guard.ClockedPerson(user_id=uuid.uuid4(), label="Marco Mechaniker")
    monkeypatch.setattr(work_guard, "people_clocked_on_job", lambda db, *, tenant_id, job_id: [person])
    _ok(add_position(client, betrieb, order, job_id, kind="text", text="Hinweis"))


def test_the_answer_is_its_own_record(client, db_session, betrieb):
    order, job_id = _accepted(client, db_session, betrieb)
    row = db_session.scalar(select(ServiceOrderJobAnswer))
    assert row is not None
    assert (str(row.job_id), row.tenant_id, row.service_order_id) == (
        job_id, betrieb.id, db_session.get(ServiceOrder, uuid.UUID(order["id"])).id
    )

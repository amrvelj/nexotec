"""KAN-218 — offboarding (Dealer Administration FR-A-45; Anto, 2026-11-13,
Q2 B and Q3 A; 2026-11-14: Q3 A, no automatic deletion).

Platform staff offboard a Betrieb only after its manager acknowledged an
export produced after the Betrieb's last write; offboarding sets
`offboarded`, the retention date (ten full years from the offboarding date)
and deactivates every user. The archive gate then answers 404 to the
Betrieb's principals everywhere; a former manager's copy is staff's archive
export, audited with the requester and the reason. Offboarding states the
fact `platform.dealership.offboarded` in its own transaction (its consumers:
`test_kan218_offboarding_withdraws_listings_and_connections.py`). Nothing
deletes the archive: once its last day has passed, platform's nightly
reconciliation reports the Betrieb as due for manual deletion.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.config import get_settings
from app.core.outbox_model import OutboxMessage
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.services.offboarding import retention_end, retention_until_for
from tests.data_export_support import (
    betrieb_with_manager,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    exported_and_acknowledged,
    get_export,
    idem,
    produce,
    request_export,
    staff,
)
from tests.identity_support import platform_admin_headers
from tests.membership_support import bearer, create_dealership, create_user, grant, token


def _dealership_version(client, dealership_id: str) -> int:
    return client.get(f"/v1/dealerships/{dealership_id}", headers=staff()).json()["version"]


def _offboard(client, dealership_id: str, *, headers=None, version=None, **body):
    payload = {"confirmation": True, "reason": "Vertrag gekündigt", **body}
    return client.post(
        f"/v1/platform/dealerships/{dealership_id}/offboard",
        json=payload,
        headers=idem(
            headers or staff(),
            **{"If-Match": str(version if version is not None else _dealership_version(client, dealership_id))},
        ),
    )


def _reason(response) -> str:
    return response.json()["error"]["details"]["reason"]


# --- refusals ------------------------------------------------------------------------


def test_offboarding_is_refused_without_an_export(client, export_store):
    dealership_id, _, _ = betrieb_with_manager(client)

    response = _offboard(client, dealership_id)

    assert response.status_code == 409, response.text
    assert _reason(response) == "export_not_acknowledged"
    assert client.get(f"/v1/dealerships/{dealership_id}", headers=staff()).json()["status"] == "pending_onboarding"


def test_offboarding_is_refused_with_an_export_nobody_acknowledged(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    job = request_export(client, dealership_id, headers)
    produce(engine, job["id"])

    response = _offboard(client, dealership_id)

    assert response.status_code == 409
    assert _reason(response) == "export_not_acknowledged"


def test_offboarding_is_refused_when_the_betrieb_wrote_after_its_export(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    exported_and_acknowledged(client, engine, dealership_id, headers)
    changed = client.patch(
        f"/v1/dealerships/{dealership_id}",
        json={"tradeName": "Nach dem Export"},
        headers={**headers, "If-Match": str(_dealership_version(client, dealership_id))},
    )
    assert changed.status_code == 200, changed.text

    response = _offboard(client, dealership_id)

    assert response.status_code == 409
    assert _reason(response) == "export_out_of_date"
    assert response.json()["error"]["details"]["lastWriteAt"] > response.json()["error"]["details"]["producedAt"]
    state = client.get(f"/v1/platform/dealerships/{dealership_id}/offboarding", headers=staff()).json()
    assert state["refusal"] == "export_out_of_date"


def test_a_deleted_record_after_the_export_makes_it_out_of_date(client, export_store, engine):
    """A deletion leaves no stamp on any row — its audit row is the write."""

    dealership_id, _, headers = betrieb_with_manager(client)
    created = client.post(
        f"/v1/dealerships/{dealership_id}/bank-accounts",
        json={"iban": "CH9300762011623852957", "bankName": "Bank", "accountHolder": "Garage Muster AG"},
        headers=idem(headers),
    )
    assert created.status_code == 201, created.text
    exported_and_acknowledged(client, engine, dealership_id, headers)
    deleted = client.delete(
        f"/v1/dealerships/{dealership_id}/bank-accounts/{created.json()['id']}",
        headers={**headers, "If-Match": str(created.json()["version"])},
    )
    assert deleted.status_code == 204, deleted.text

    assert _reason(_offboard(client, dealership_id)) == "export_out_of_date"


def test_only_staff_with_a_second_factor_offboard(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    exported_and_acknowledged(client, engine, dealership_id, headers)

    assert _offboard(client, dealership_id, headers=headers).status_code == 403
    assert _offboard(client, dealership_id, headers=platform_admin_headers(mfa=False)).status_code == 403


def test_offboarding_needs_if_match_and_a_confirmation(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    exported_and_acknowledged(client, engine, dealership_id, headers)

    no_if_match = client.post(
        f"/v1/platform/dealerships/{dealership_id}/offboard",
        json={"confirmation": True},
        headers=idem(staff()),
    )
    assert no_if_match.status_code == 400
    assert _offboard(client, dealership_id, version=0).status_code == 409
    unconfirmed = _offboard(client, dealership_id, confirmation=False)
    assert unconfirmed.status_code == 422
    assert _reason(unconfirmed) == "offboarding_confirmation_required"


def test_offboarding_is_refused_when_a_user_is_a_sisters_last_manager(client, export_store, engine):
    dealership_id, manager, headers = betrieb_with_manager(client)
    sister_id = create_dealership(client, "Garage Schwester AG", group_of=dealership_id)
    assert grant(client, dealership_id, manager, sister_id, is_dealer_manager=True).status_code == 201
    exported_and_acknowledged(client, engine, dealership_id, headers)

    response = _offboard(client, dealership_id)

    assert response.status_code == 409
    assert _reason(response) == "user_last_manager_of_sister"


# --- the offboarding -----------------------------------------------------------------


def test_offboarding_sets_the_status_the_retention_date_and_deactivates_every_user(
    client, export_store, engine, db_session
):
    dealership_id, manager, headers = betrieb_with_manager(client)
    colleague = create_user(client, dealership_id, is_dealer_manager=False)
    other_id, other_manager, _ = betrieb_with_manager(client, "Garage Anders AG")
    assert grant(client, other_id, other_manager, dealership_id).status_code == 201
    export = exported_and_acknowledged(client, engine, dealership_id, headers)

    response = _offboard(client, dealership_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "offboarded"
    # The Swiss calendar day of the offboarding (the day reconciliation judges by).
    today = dt.datetime.fromisoformat(body["offboardedAt"]).astimezone(ZoneInfo(get_settings().session_timezone)).date()
    assert body["retentionUntil"] == today.replace(year=today.year + 10).isoformat()
    users = db_session.scalars(select(User).where(User.tenant_id == uuid.UUID(dealership_id))).all()
    assert {user.id for user in users} == {uuid.UUID(manager["id"]), uuid.UUID(colleague["id"])}
    assert all(user.status == UserStatus.DEACTIVATED for user in users)
    assert all(user.deactivation_reason == "dealership_offboarded" for user in users)
    membership = db_session.scalar(
        select(DealershipMembership).where(DealershipMembership.dealership_id == uuid.UUID(dealership_id))
    )
    assert membership.valid_to is not None
    # The member keeps their own Betrieb.
    assert db_session.get(User, uuid.UUID(other_manager["id"])).status != UserStatus.DEACTIVATED
    row = db_session.scalar(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(dealership_id), AuditEvent.action == "offboarded")
    )
    assert row.reason == "Vertrag gekündigt"
    assert row.after["export_id"] == export["id"]
    assert row.after["users_deactivated"] == 2
    [fact] = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "platform.dealership.offboarded",
            OutboxMessage.aggregate_id == uuid.UUID(dealership_id),
        )
    ).all()
    assert fact.payload == {"dealershipId": dealership_id, "offboardedAt": body["offboardedAt"].replace("Z", "+00:00")}

    again = _offboard(client, dealership_id)
    assert again.status_code == 409
    assert _reason(again) == "already_offboarded"


def test_ten_full_years_from_the_offboarding_date():
    assert retention_end(dt.date(2026, 11, 14)) == dt.date(2036, 11, 14)
    assert retention_end(dt.date(2028, 2, 29)) == dt.date(2038, 3, 1)


# --- no automatic deletion: reconciliation reports what is due -----------------------

DUE_CHECK = "dealership.archive_due_for_manual_deletion"


def _due_findings(db_session) -> set[uuid.UUID]:
    """This check's findings in a fresh run (the run raises on any finding)."""

    from app.core.reconciliation import ReconciliationAlarm
    from app.core.reconciliation_model import ReconciliationOrphan
    from app.platform import reconciliation as platform_reconciliation

    try:
        run_id = platform_reconciliation.run(db_session).id
    except ReconciliationAlarm as alarm:
        run_id = alarm.run.id
    return set(
        db_session.scalars(
            select(ReconciliationOrphan.source_row_id).where(
                ReconciliationOrphan.run_id == run_id, ReconciliationOrphan.check_label == DUE_CHECK
            )
        ).all()
    )


def _archived(db_session, client, legal_name: str, *, retention_until: dt.date | None) -> uuid.UUID:
    dealership = db_session.get(Dealership, uuid.UUID(create_dealership(client, legal_name)))
    dealership.status = DealershipStatus.OFFBOARDED
    dealership.offboarded_at = dt.datetime.now(dt.UTC)
    dealership.retention_until = retention_until
    db_session.commit()
    return dealership.id


def test_reconciliation_reports_an_archive_past_its_last_day_as_due_for_manual_deletion(client, db_session):
    today = dt.datetime.now(ZoneInfo(get_settings().session_timezone)).date()
    past_due = _archived(db_session, client, "Garage Vorbei AG", retention_until=today - dt.timedelta(days=1))
    last_day = _archived(db_session, client, "Garage Heute AG", retention_until=today)
    not_yet = _archived(db_session, client, "Garage Später AG", retention_until=today + dt.timedelta(days=365))
    live = uuid.UUID(create_dealership(client, "Garage Offen AG"))

    findings = _due_findings(db_session)

    assert past_due in findings
    assert last_day not in findings, "the archive is kept through its last day"
    assert not_yet not in findings
    assert live not in findings
    assert findings == {past_due}
    # Reported, never repaired: the Betrieb is still there, still offboarded.
    db_session.expire_all()
    assert db_session.get(Dealership, past_due).status == DealershipStatus.OFFBOARDED


# --- the archive gate ----------------------------------------------------------------


def test_an_offboarded_betriebs_principals_get_404_everywhere(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    other_id, _, other_headers = betrieb_with_manager(client, "Garage Anders AG")
    exported_and_acknowledged(client, engine, dealership_id, headers)
    assert client.get("/v1/customers", headers=headers).status_code == 200
    assert _offboard(client, dealership_id).status_code == 200

    sales = bearer(token(AccessRole.SALES, tenant_id=dealership_id))
    for principal in (headers, sales):
        for method, path in (
            ("GET", f"/v1/dealerships/{dealership_id}"),
            ("GET", f"/v1/dealerships/{dealership_id}/exports"),
            ("POST", f"/v1/dealerships/{dealership_id}/exports"),
            ("GET", "/v1/customers"),
            ("GET", "/v1/auth/me"),
            ("PATCH", f"/v1/dealerships/{dealership_id}"),
        ):
            response = client.request(method, path, headers=idem(principal, **{"If-Match": "1"}), json={})
            assert response.status_code == 404, (method, path, response.status_code)
    # Another Betrieb and staff are untouched.
    assert client.get("/v1/customers", headers=other_headers).status_code == 200
    assert client.get(f"/v1/dealerships/{other_id}", headers=other_headers).status_code == 200
    assert client.get(f"/v1/dealerships/{dealership_id}", headers=staff()).json()["status"] == "offboarded"


# --- Q2 B: staff produce the archive export --------------------------------------------


def test_staff_produce_an_archive_export_with_a_reason_audited(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    exported_and_acknowledged(client, engine, dealership_id, headers)
    staff_user = uuid.uuid4()
    staff_headers = platform_admin_headers(user_id=staff_user)
    assert _offboard(client, dealership_id).status_code == 200

    response = client.post(
        f"/v1/platform/dealerships/{dealership_id}/exports",
        json={"reason": "Ehemaliger Geschäftsführer bittet um eine Kopie (Ticket 4711)"},
        headers=idem(staff_headers),
    )

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["kind"] == "archive"
    assert job["requestedBy"] == str(staff_user)
    produce(engine, job["id"])
    download = client.get(f"/v1/platform/dealerships/{dealership_id}/exports/{job['id']}/download", headers=staff())
    assert download.status_code == 200
    assert download.content[:2] == b"PK"
    listed = client.get(f"/v1/platform/dealerships/{dealership_id}/exports", headers=staff()).json()["items"]
    assert {item["kind"] for item in listed} == {"self_service", "archive"}
    requested = db_session.scalar(
        select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(job["id"]), AuditEvent.action == "requested")
    )
    assert requested.actor_id == staff_user
    assert requested.reason.startswith("Ehemaliger Geschäftsführer")


def test_an_archive_export_needs_a_reason_staff_and_an_offboarded_betrieb(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)

    live = client.post(
        f"/v1/platform/dealerships/{dealership_id}/exports", json={"reason": "Kopie"}, headers=idem(staff())
    )
    assert live.status_code == 409
    assert _reason(live) == "dealership_not_offboarded"
    assert (
        client.post(f"/v1/platform/dealerships/{dealership_id}/exports", json={}, headers=idem(staff())).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/platform/dealerships/{dealership_id}/exports", json={"reason": "Kopie"}, headers=idem(headers)
        ).status_code
        == 403
    )


def test_staff_cannot_download_the_managers_own_export_on_the_platform_path(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    export = exported_and_acknowledged(client, engine, dealership_id, headers)

    response = client.get(f"/v1/platform/dealerships/{dealership_id}/exports/{export['id']}/download", headers=staff())

    assert response.status_code == 404


def test_the_offboarding_state_for_the_platform_page(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    before = client.get(f"/v1/platform/dealerships/{dealership_id}/offboarding", headers=staff())
    assert before.status_code == 200
    assert before.json()["refusal"] == "export_not_acknowledged"
    export = exported_and_acknowledged(client, engine, dealership_id, headers)

    ready = client.get(f"/v1/platform/dealerships/{dealership_id}/offboarding", headers=staff()).json()

    assert ready["refusal"] is None
    assert ready["acknowledgedExportId"] == export["id"]
    assert ready["lastWriteAt"] < ready["acknowledgedExportProducedAt"]
    assert db_session.get(Dealership, uuid.UUID(dealership_id)).status != DealershipStatus.OFFBOARDED
    assert get_export(client, dealership_id, export["id"], headers)["acknowledgedAt"] is not None


def test_the_retention_date_is_taken_on_the_swiss_calendar_day():
    """Offboarded at 00:30 Swiss time (23:30 UTC the day before): the archive
    keeps ten full Swiss years, as the nightly report judges them."""

    just_after_swiss_midnight = dt.datetime(2026, 11, 13, 23, 30, tzinfo=dt.UTC)
    assert retention_until_for(just_after_swiss_midnight) == dt.date(2036, 11, 14)

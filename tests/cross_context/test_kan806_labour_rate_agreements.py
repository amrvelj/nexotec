"""KAN-806: a fixed labour rate agreed with a contact, created at the
composition root (`app/api/v1/labour_rate_agreements.py`): it reads the
contact (contact's) and writes the agreement (platform's) — another group's
contact is 404, a merged one 409, the label is stored; the answer then puts
the agreement first for that invoice recipient (check 11); a Betrieb still
being set up reaches it; platform's reconciliation watches the reference."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.contact.models.contact import Contact, ContactLifecycleStatus
from app.core.auth import create_access_token
from app.core.permissions import ModuleCode
from app.core.reconciliation import find_rows_in_state
from app.platform import reconciliation as platform_reconciliation
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.labour_rate import LabourRateAgreement
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services import labour_rates as rates_service
from tests import setup_checklist_support as s
from tests.identity_support import create_dealership
from tests.token_support import dealer_group_of


def _today():
    # Asked per use, so a run across midnight in Zurich still agrees with the server.
    return rates_service.today()


def _headers(user_id: str, dealership_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.UUID(user_id), tenant_id=dealership_id, group_id=dealer_group_of(dealership_id),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _betrieb(client, db, *, pending=False):
    if pending:
        dealership_id = s.pending_dealership(client, db, modules=(ModuleCode.AFTERSALES,))
    else:
        dealership_id = uuid.UUID(create_dealership(client))
        db.add(ModuleEntitlement(dealership_id=dealership_id, module=ModuleCode.AFTERSALES))
        db.commit()
    boss = s.manager(client, db, dealership_id)
    headers = _headers(boss["id"], dealership_id)
    put = client.put(
        f"/v1/dealerships/{dealership_id}/setting-values/labour_rates.rates_entered",
        json={"value": {"choice": "net"}}, headers={**headers, "If-Match": "0"},
    )
    assert put.status_code == 200, put.text
    category = client.post(f"/v1/dealerships/{dealership_id}/labour-rate-categories", json={"name": "Mechanik"},
                           headers=headers)
    assert category.status_code == 201, category.text
    return dealership_id, headers, category.json()["id"]


def _contact(db, dealership_id, *, company="Helvetia Versicherungen AG", status=ContactLifecycleStatus.ACTIVE):
    contact = Contact(
        group_id=dealer_group_of(dealership_id), contact_number=f"K-{uuid.uuid4().hex[:10]}", contact_type="business", company_name=company, language="de",
        lifecycle_status=status,
    )
    db.add(contact)
    db.commit()
    return contact


def _agree(client, headers, dealership_id, category_id, contact_id, **overrides):
    body = {"contactId": str(contact_id), "categoryId": category_id, "validFrom": _today().isoformat(),
            "amount": "120.00", "entered": "net", **overrides}
    return client.post(f"/v1/dealerships/{dealership_id}/labour-rate-agreements", json=body, headers=headers)


@pytest.fixture()
def betrieb(client, db_session):
    return _betrieb(client, db_session)


def test_an_agreement_with_a_contact_of_the_group_comes_first_in_the_answer(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    insurer = _contact(db_session, dealership_id)
    created = _agree(client, headers, dealership_id, category_id, insurer.id)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["contactLabel"], body["kind"], body["amount"], body["status"]) == (
        "Helvetia Versicherungen AG", "rate", "120.0000", "in_force",
    )
    seat = db_session.scalar(select(Location.id).where(Location.tenant_id == dealership_id))
    answer = client.get(
        f"/v1/dealerships/{dealership_id}/labour-rate-answer",
        params={"categoryId": category_id, "locationId": str(seat), "invoiceRecipientContactId": str(insurer.id)},
        headers=headers,
    ).json()
    assert (answer["source"], answer["rateId"], answer["amount"]) == ("agreement", body["id"], "120.0000")

    listing = client.get(f"/v1/dealerships/{dealership_id}/labour-rate-agreements", headers=headers)
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["items"]] == [body["id"]]
    ended = _agree(client, headers, dealership_id, category_id, insurer.id, kind="end", amount=None, entered=None,
                   validFrom=(_today() + dt.timedelta(days=30)).isoformat())
    assert ended.status_code == 201, ended.text
    assert ended.json()["status"] == "scheduled"


def test_another_groups_contact_is_not_found(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    other = uuid.UUID(create_dealership(client))
    stranger = _contact(db_session, other)
    assert _agree(client, headers, dealership_id, category_id, stranger.id).status_code == 404
    assert db_session.scalar(select(LabourRateAgreement.id)) is None


def test_a_merged_contact_is_refused(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    merged = _contact(db_session, dealership_id, status=ContactLifecycleStatus.MERGED)
    refused = _agree(client, headers, dealership_id, category_id, merged.id)
    assert (refused.status_code, refused.json()["error"]["details"]["reason"]) == (
        409, "labour_rate_agreement_contact_merged",
    )


def test_a_betrieb_being_set_up_reaches_the_agreement(client, db_session):
    dealership_id, headers, category_id = _betrieb(client, db_session, pending=True)
    insurer = _contact(db_session, dealership_id)
    assert _agree(client, headers, dealership_id, category_id, insurer.id).status_code == 201


def test_reconciliation_reports_an_agreement_naming_a_merged_contact(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    insurer = _contact(db_session, dealership_id)
    created = _agree(client, headers, dealership_id, category_id, insurer.id).json()
    insurer.lifecycle_status = ContactLifecycleStatus.MERGED
    insurer.updated_at = insurer.updated_at - dt.timedelta(days=2)
    db_session.commit()
    check = next(
        c for c in platform_reconciliation.CHECKS
        if c.label == "labour_rate_agreement.contact_id names a merged contact"
    )
    assert [str(row) for row, _ in find_rows_in_state(db_session, check)] == [created["id"]]
    outside = next(c for c in platform_reconciliation.CHECKS if c.label.endswith("outside the business's group"))
    assert find_rows_in_state(db_session, outside) == []


def test_the_agreements_page_by_cursor_latest_start_first(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    first = _contact(db_session, dealership_id, company="Fleet AG")
    second = _contact(db_session, dealership_id, company="Importeur AG")
    _agree(client, headers, dealership_id, category_id, first.id)
    later = _agree(client, headers, dealership_id, category_id, second.id,
                   validFrom=(_today() + dt.timedelta(days=3)).isoformat()).json()
    path = f"/v1/dealerships/{dealership_id}/labour-rate-agreements"
    page = client.get(path, params={"limit": 1}, headers=headers).json()
    assert [item["id"] for item in page["items"]] == [later["id"]] and page["nextCursor"]
    rest = client.get(path, params={"limit": 1, "cursor": page["nextCursor"]}, headers=headers).json()
    assert [item["contactLabel"] for item in rest["items"]] == ["Fleet AG"] and rest["nextCursor"] is None


def test_reconciliation_reports_an_agreement_naming_another_groups_contact(client, db_session, betrieb):
    dealership_id, headers, category_id = betrieb
    insurer = _contact(db_session, dealership_id)
    created = _agree(client, headers, dealership_id, category_id, insurer.id).json()
    outside = next(c for c in platform_reconciliation.CHECKS if c.label.endswith("outside the business's group"))
    assert find_rows_in_state(db_session, outside) == []
    other = uuid.UUID(create_dealership(client))
    insurer.group_id = dealer_group_of(other)  # what the composition root refuses (404)
    db_session.commit()
    assert [str(row) for row, _ in find_rows_in_state(db_session, outside)] == [created["id"]]

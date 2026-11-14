"""KAN-214 (D-A-21, FR-A-26; Anto, 2026-11-13, Q2 A; 2026-11-14, Q5 A): the
setup checklist is derived per entitlement set, each item from its owner's
data, never ticked; an item another context owns joins through the core
port; a broken prerequisite refuses only its own action, naming the item."""

import uuid

import pytest

from app.core.permissions import ModuleCode
from app.core.setup_checklist import (
    ChecklistContribution,
    register_checklist_contribution,
    unregister_checklist_contribution,
)
from app.platform.identity.models.user import User, UserStatus
from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.models.dealership_bank_account import DealershipBankAccount
from app.platform.services import setup_checklist
from tests import setup_checklist_support as s

ALL_ITEMS = {"branding", "vat_and_rounding", "bank_account", "number_ranges", "location_opening_hours", "users"}


def _items(db, dealership_id) -> dict[str, bool]:
    dealership = db.get(Dealership, dealership_id)
    return {item.code: item.complete for item in setup_checklist.derive(db, dealership).items}


def test_a_betrieb_with_sales_has_every_item_and_none_is_complete(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    assert _items(db_session, dealership_id) == dict.fromkeys(ALL_ITEMS, False)


def test_a_workshop_without_sales_has_no_location_hours_item(client, db_session):
    """Q2 A: opening hours (and later the publishing profile) apply only with `sales`."""

    dealership_id = s.pending_dealership(client, db_session, modules=(ModuleCode.AFTERSALES,))
    assert set(_items(db_session, dealership_id)) == ALL_ITEMS - {"location_opening_hours"}


def test_each_item_is_completed_by_its_owners_data(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    user = s.manager(client, db_session, dealership_id)
    assert _items(db_session, dealership_id)["users"] is True  # the manager alone (one-person Betrieb)

    s.complete_branding(db_session, dealership_id)
    assert _items(db_session, dealership_id)["branding"] is True
    s.complete_vat(db_session, dealership_id)
    assert _items(db_session, dealership_id)["vat_and_rounding"] is True
    s.complete_bank_account(db_session, dealership_id)
    assert _items(db_session, dealership_id)["bank_account"] is True
    s.complete_number_ranges(db_session, dealership_id, uuid.UUID(user["id"]))
    assert _items(db_session, dealership_id)["number_ranges"] is True
    s.complete_opening_hours(db_session, dealership_id)
    assert all(_items(db_session, dealership_id).values())


def test_an_invited_manager_does_not_meet_users(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.manager(client, db_session, dealership_id, active=False)
    assert _items(db_session, dealership_id)["users"] is False


def test_vat_liable_needs_the_vat_number(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.complete_vat(db_session, dealership_id)
    dealership = db_session.get(Dealership, dealership_id)
    dealership.vat_liable = True
    dealership.vat_number = None
    db_session.commit()
    assert _items(db_session, dealership_id)["vat_and_rounding"] is False


def test_a_bank_account_that_is_not_the_default_does_not_complete_the_item(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    account = s.complete_bank_account(db_session, dealership_id)
    account.is_default = False
    db_session.commit()
    assert _items(db_session, dealership_id)["bank_account"] is False


def test_a_deactivated_location_does_not_carry_opening_hours(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.complete_opening_hours(db_session, dealership_id)
    s.seat_location(db_session, dealership_id).is_active = False
    db_session.commit()
    assert _items(db_session, dealership_id)["location_opening_hours"] is False


@pytest.fixture()
def contributed_item():
    """An item owned above platform, through the core port (Q5 A's extension point)."""

    state = {"complete": False}
    register_checklist_contribution(
        ChecklistContribution(
            code="publishing_profile",
            applies_to_modules=frozenset({ModuleCode.SALES.value}),
            blocks=setup_checklist.BlockedAction.MARKETPLACE_PUBLISHING.value,
            link="/administration/publishing",
            is_complete=lambda db, dealership_id: state["complete"],
        )
    )
    yield state
    unregister_checklist_contribution("publishing_profile")


def test_a_contributed_item_joins_the_checklist_where_it_applies(client, db_session, contributed_item):
    with_sales = s.pending_dealership(client, db_session)
    workshop = s.pending_dealership(client, db_session, modules=(ModuleCode.AFTERSALES,))
    assert _items(db_session, with_sales)["publishing_profile"] is False
    assert "publishing_profile" not in _items(db_session, workshop)
    contributed_item["complete"] = True
    assert _items(db_session, with_sales)["publishing_profile"] is True


def test_no_contribution_is_registered_until_the_publishing_profile_ships(client, db_session):
    """Q5 A: a Betrieb with `sales` activates without a publishing-profile item today."""

    dealership_id = s.pending_dealership(client, db_session)
    assert "publishing_profile" not in _items(db_session, dealership_id)


def test_the_checklist_endpoint_is_the_managers(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    clerk = s.manager(client, db_session, dealership_id, isDealerManager=False)

    response = client.get(f"/v1/dealerships/{dealership_id}/setup-checklist", headers=s.headers_for(boss, dealership_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending_onboarding"
    assert body["complete"] is False
    assert body["mayActivate"] is False
    assert {item["code"] for item in body["items"]} == ALL_ITEMS
    links = {item["code"]: item["link"] for item in body["items"]}
    assert links["number_ranges"] == "/administration/number-ranges"
    blocks = {item["code"]: item["blocks"] for item in body["items"]}
    assert blocks["bank_account"] == "invoice_issue"
    assert blocks["branding"] is None

    refused = client.get(
        f"/v1/dealerships/{dealership_id}/setup-checklist", headers=s.headers_for(clerk, dealership_id, manager=False)
    )
    assert refused.status_code == 403

    other = s.pending_dealership(client, db_session)
    assert client.get(f"/v1/dealerships/{other}/setup-checklist", headers=s.headers_for(boss, dealership_id)).status_code == 404


def test_the_manager_confirms_the_number_ranges_and_the_list_says_when(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    headers = s.headers_for(boss, dealership_id)
    before = client.get(f"/v1/dealerships/{dealership_id}/number-ranges", headers=headers).json()
    assert before["confirmedAt"] is None

    response = client.post(f"/v1/dealerships/{dealership_id}/number-range-confirmations", headers=headers)
    assert response.status_code == 201, response.text

    after = client.get(f"/v1/dealerships/{dealership_id}/number-ranges", headers=headers).json()
    assert after["confirmedAt"] is not None
    assert all(item["frozenAt"] is None for item in after["items"])  # confirming freezes nothing
    assert _items(db_session, dealership_id)["number_ranges"] is True


def test_a_colleague_cannot_confirm_the_number_ranges(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    clerk = s.manager(client, db_session, dealership_id, isDealerManager=False)
    response = client.post(
        f"/v1/dealerships/{dealership_id}/number-range-confirmations",
        headers=s.headers_for(clerk, dealership_id, manager=False),
    )
    assert response.status_code == 403


# --- after activation: the item blocks only its own action (FR-A-26) ------------------------


def test_require_item_for_names_the_item_and_its_link(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    with pytest.raises(Exception) as refused:
        setup_checklist.require_item_for(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)
    assert refused.value.details == {
        "reason": "setup_checklist_item_missing",
        "item": "vat_and_rounding",
        "link": "/administration/business",
        "action": "invoice_issue",
    }
    s.complete_vat(db_session, dealership_id)
    with pytest.raises(Exception) as still:
        setup_checklist.require_item_for(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)
    assert still.value.details["item"] == "bank_account"  # an invoice without IBAN is refused with the item
    s.complete_bank_account(db_session, dealership_id)
    setup_checklist.require_item_for(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)
    # Branding blocks activation only, never an action after it.
    assert setup_checklist.blocking_item(db_session, dealership_id, "anything_else") is None


def test_deleting_the_default_bank_account_breaks_invoicing_again(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    s.complete_vat(db_session, dealership_id)
    account = s.complete_bank_account(db_session, dealership_id)
    assert setup_checklist.blocking_item(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE) is None
    db_session.delete(db_session.get(DealershipBankAccount, account.id))
    db_session.commit()
    item = setup_checklist.blocking_item(db_session, dealership_id, setup_checklist.BlockedAction.INVOICE_ISSUE)
    assert item is not None and item.code == "bank_account"


def test_a_suspended_manager_does_not_count(client, db_session):
    dealership_id = s.pending_dealership(client, db_session)
    boss = s.manager(client, db_session, dealership_id)
    db_session.get(User, uuid.UUID(boss["id"])).status = UserStatus.SUSPENDED
    db_session.commit()
    assert _items(db_session, dealership_id)["users"] is False

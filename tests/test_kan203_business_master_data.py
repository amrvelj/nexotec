"""KAN-203 — Business master data: legal identity, Händlerschild, ASTRA,
associations, bank accounts, fiscal defaults (Dealer Administration PRD
"Dealership master data"; D-A-22, D-A-25, D-A-27, FR-A-25).

What is pinned here:
- bank accounts: a valid CH IBAN saves compact, a bad checksum is a 422, a
  QR-IBAN is told apart by its IID and refused in the wrong field, `qrr`
  needs the QR-IBAN; exactly one default (chosen explicitly — Anto,
  2026-10-24); every write audit-logged with before and after;
- the matrix row "Banking details": manager, finance and the auditor read,
  the manager writes, anyone else gets a 404 on every bank-account route
  (ADR-100), another Betrieb's manager too;
- settings: every Betrieb reads the shipped defaults (CHF, 0.05, 30 days,
  01-01, KMU, month-end lock), no `vatMethod`; any role reads, the manager
  writes, audited;
- the legal identity needs `confirmation: true` (422 `confirmation_required`
  without), `auditNote` lands as the audit reason; `legalForm` and
  `associationMemberships` are checked against the seeded lists.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.auth import AccessRole
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType
from app.platform.models.dealership_bank_account import DealershipBankAccount
from app.platform.models.dealership_settings import DealershipSettings
from app.platform.reference_data_seed import BUSINESS_LISTS
from tests.dealership_support import VALID_UID, seed_business_reference_lists, valid_uid
from tests.test_dealership import VALID_ADDRESS, _bearer, _create_dealership, _token

# Real-shaped IBANs: a plain CH IBAN (SIX's documentation example), a QR-IBAN
# (IID 31999, SIX QR-bill IG example), a Liechtenstein one and a German one.
PLAIN_IBAN = "CH93 0076 2011 6238 5295 7"
PLAIN_IBAN_COMPACT = "CH9300762011623852957"
QR_IBAN = "CH44 3199 9123 0008 8901 2"
QR_IBAN_COMPACT = "CH4431999123000889012"
DE_IBAN = "DE89 3704 0044 0532 0130 00"
BAD_CHECKSUM_IBAN = "CH93 0076 2011 6238 5295 8"

_MIGRATION = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "platform"


def _manager(dealership_id: str) -> dict[str, str]:
    return _bearer(_token(tenant_id=uuid.UUID(dealership_id), is_dealer_manager=True))


def _role(dealership_id: str, role: AccessRole) -> dict[str, str]:
    return _bearer(_token(role, tenant_id=uuid.UUID(dealership_id)))


def _account_payload(**overrides):
    payload = {
        "accountHolder": "Garage Musterbetrieb AG",
        "bankName": "Zürcher Kantonalbank",
        "iban": PLAIN_IBAN,
    }
    payload.update(overrides)
    return payload


def _create_account(client, dealership_id: str, **overrides):
    response = client.post(
        f"/v1/dealerships/{dealership_id}/bank-accounts",
        json=_account_payload(**overrides),
        headers=_manager(dealership_id),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _audit(client, dealership_id: str) -> list[dict]:
    log = client.get(f"/v1/dealerships/{dealership_id}/audit-log", headers=_manager(dealership_id))
    assert log.status_code == 200, log.text
    return log.json()["items"]


# --- bank accounts: IBAN rules ------------------------------------------------


def test_a_valid_swiss_iban_is_saved_compact_and_the_create_is_audited(client):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"], bic="ZKBKCHZZ80A", label="Hauptkonto")

    assert account["iban"] == PLAIN_IBAN_COMPACT
    assert account["qrIban"] is None
    assert account["qrReferenceType"] == "non"
    assert account["isDefault"] is False  # chosen explicitly, never promoted
    assert account["version"] == 1

    create_event = next(e for e in _audit(client, dealership["id"]) if e["entityType"] == "dealership_bank_account")
    assert create_event["action"] == "create"
    assert create_event["after"]["iban"] == PLAIN_IBAN_COMPACT
    assert create_event["before"] is None


def test_an_iban_with_a_wrong_checksum_is_a_422(client):
    dealership = _create_dealership(client)
    response = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts",
        json=_account_payload(iban=BAD_CHECKSUM_IBAN),
        headers=_manager(dealership["id"]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "unprocessable_entity"


def test_a_qr_iban_is_refused_in_the_iban_field_and_a_plain_iban_in_the_qr_iban_field(client):
    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])

    as_iban = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts", json=_account_payload(iban=QR_IBAN), headers=headers
    )
    assert as_iban.status_code == 422, as_iban.text
    assert "QR-IBAN" in as_iban.text

    as_qr = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts",
        json=_account_payload(qrIban=PLAIN_IBAN, qrReferenceType="qrr"),
        headers=headers,
    )
    assert as_qr.status_code == 422, as_qr.text

    foreign_as_qr = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts",
        json=_account_payload(qrIban=DE_IBAN, qrReferenceType="qrr"),
        headers=headers,
    )
    assert foreign_as_qr.status_code == 422, foreign_as_qr.text


def test_a_qr_iban_with_the_qr_reference_round_trips(client):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"], qrIban=QR_IBAN, qrReferenceType="qrr")
    assert account["qrIban"] == QR_IBAN_COMPACT
    assert account["qrReferenceType"] == "qrr"


def test_a_foreign_iban_is_accepted_as_the_plain_iban(client):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"], iban=DE_IBAN)
    assert account["iban"] == "DE89370400440532013000"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"qrReferenceType": "qrr"}, "qr_reference_needs_qr_iban"),
        ({"qrIban": QR_IBAN}, "qr_iban_needs_qr_reference"),
        ({"qrIban": QR_IBAN, "qrReferenceType": "scor"}, "qr_iban_needs_qr_reference"),
        ({"creditorReferencePrefix": "GARAGE1"}, "creditor_prefix_needs_scor"),
    ],
)
def test_qr_iban_and_reference_type_must_agree(client, payload, reason):
    dealership = _create_dealership(client)
    response = client.post(
        f"/v1/dealerships/{dealership['id']}/bank-accounts",
        json=_account_payload(**payload),
        headers=_manager(dealership["id"]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason


def test_the_structured_creditor_reference_takes_a_prefix(client):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"], qrReferenceType="scor", creditorReferencePrefix="GARAGE1")
    assert account["creditorReferencePrefix"] == "GARAGE1"


def test_a_patch_is_judged_on_the_merged_account_not_the_patch_alone(client):
    """Setting `qrr` on an account that has no QR-IBAN is a 422 even though the
    PATCH body alone looks fine."""

    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"])
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}",
        json={"qrReferenceType": "qrr"},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "qr_reference_needs_qr_iban"


# --- bank accounts: the one default ----------------------------------------------


def test_the_default_is_chosen_explicitly_and_moves_with_the_flag(client):
    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])
    first = _create_account(client, dealership["id"], label="Hauptkonto")
    assert first["isDefault"] is False

    made_default = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{first['id']}",
        json={"isDefault": True},
        headers={**headers, "If-Match": "1"},
    )
    assert made_default.status_code == 200, made_default.text
    assert made_default.json()["isDefault"] is True

    second = _create_account(client, dealership["id"], iban=DE_IBAN, label="Werkstatt", isDefault=True)
    assert second["isDefault"] is True

    listing = client.get(f"/v1/dealerships/{dealership['id']}/bank-accounts", headers=headers).json()["items"]
    by_id = {item["id"]: item for item in listing}
    assert by_id[first["id"]]["isDefault"] is False
    assert by_id[second["id"]]["isDefault"] is True
    assert listing[0]["id"] == second["id"]  # the default lists first
    # The demotion is its own audited write on the first account.
    demotion = [
        e
        for e in _audit(client, dealership["id"])
        if e["entityId"] == first["id"] and e["action"] == "update" and e["after"] == {"is_default": False}
    ]
    assert len(demotion) == 1 and demotion[0]["before"] == {"is_default": True}


def test_two_defaults_cannot_coexist_in_the_table(db_session):
    group = DealerGroup(name="G")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage AG",
        franchise_type=FranchiseType.INDEPENDENT,
        phone="+41441234567",
        uid=valid_uid(),
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
    )
    db_session.add(dealership)
    db_session.flush()
    for iban in (PLAIN_IBAN_COMPACT, "DE89370400440532013000"):
        db_session.add(
            DealershipBankAccount(
                dealership_id=dealership.id, account_holder="Garage AG", bank_name="Bank", iban=iban, is_default=True
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_default_may_be_demoted_or_deleted_leaving_none(client):
    """Anto, 2026-10-24: the default is the manager's explicit choice; a
    Betrieb without one has no invoice account yet (the checklist's business)."""

    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])
    account = _create_account(client, dealership["id"], isDefault=True)
    other = _create_account(client, dealership["id"], iban=DE_IBAN)

    deleted = client.delete(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}", headers={**headers, "If-Match": "1"}
    )
    assert deleted.status_code == 204, deleted.text
    listing = client.get(f"/v1/dealerships/{dealership['id']}/bank-accounts", headers=headers).json()["items"]
    assert [item["id"] for item in listing] == [other["id"]]
    assert listing[0]["isDefault"] is False

    delete_event = next(e for e in _audit(client, dealership["id"]) if e["action"] == "delete")
    assert delete_event["entityId"] == account["id"]
    assert delete_event["before"]["iban"] == PLAIN_IBAN_COMPACT


# --- bank accounts: update, concurrency, audit ---------------------------------


def test_an_update_is_audited_with_only_what_changed(client):
    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])
    account = _create_account(client, dealership["id"])

    no_if_match = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}", json={"bankName": "UBS"}, headers=headers
    )
    assert no_if_match.status_code == 400

    stale = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}",
        json={"bankName": "UBS"},
        headers={**headers, "If-Match": "7"},
    )
    assert stale.status_code == 409

    updated = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}",
        json={"bankName": "UBS", "accountHolder": account["accountHolder"]},
        headers={**headers, "If-Match": "1"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2

    update_event = next(
        e for e in _audit(client, dealership["id"]) if e["entityId"] == account["id"] and e["action"] == "update"
    )
    assert update_event["before"] == {"bank_name": "Zürcher Kantonalbank"}
    assert update_event["after"] == {"bank_name": "UBS"}


def test_a_null_on_a_required_bank_account_field_is_a_422(client):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"])
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}/bank-accounts/{account['id']}",
        json={"iban": None},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422


# --- bank accounts: who sees them (ADR-100) ----------------------------------------


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY, AccessRole.AFTERSALES, AccessRole.PARTS])
def test_outside_the_banking_readers_every_bank_account_route_is_a_404(client, role):
    """A salesperson's token on his own Betrieb: the accounts do not exist for
    him — not the list, not one account, not a write probe."""

    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"])
    headers = _role(dealership["id"], role)
    base = f"/v1/dealerships/{dealership['id']}/bank-accounts"

    assert client.get(base, headers=headers).status_code == 404
    assert client.get(f"{base}/{account['id']}", headers=headers).status_code == 404
    assert client.post(base, json=_account_payload(iban=DE_IBAN), headers=headers).status_code == 404
    assert (
        client.patch(f"{base}/{account['id']}", json={"bankName": "X"}, headers={**headers, "If-Match": "1"}).status_code
        == 404
    )
    assert client.delete(f"{base}/{account['id']}", headers={**headers, "If-Match": "1"}).status_code == 404
    # Nothing changed.
    listing = client.get(base, headers=_manager(dealership["id"])).json()["items"]
    assert [a["bankName"] for a in listing] == ["Zürcher Kantonalbank"]


@pytest.mark.parametrize("role", [AccessRole.FINANCE, AccessRole.AUDITOR])
def test_finance_and_the_auditor_read_but_do_not_write(client, role):
    dealership = _create_dealership(client)
    account = _create_account(client, dealership["id"])
    headers = _role(dealership["id"], role)
    base = f"/v1/dealerships/{dealership['id']}/bank-accounts"

    assert client.get(base, headers=headers).status_code == 200
    assert client.get(f"{base}/{account['id']}", headers=headers).json()["iban"] == PLAIN_IBAN_COMPACT
    assert client.post(base, json=_account_payload(iban=DE_IBAN), headers=headers).status_code == 404
    assert (
        client.patch(f"{base}/{account['id']}", json={"bankName": "X"}, headers={**headers, "If-Match": "1"}).status_code
        == 404
    )


def test_another_betriebs_manager_gets_404_on_the_accounts(client):
    dealership = _create_dealership(client)
    other = _create_dealership(client)
    account = _create_account(client, dealership["id"])
    base = f"/v1/dealerships/{dealership['id']}/bank-accounts"
    assert client.get(base, headers=_manager(other["id"])).status_code == 404
    assert client.get(f"{base}/{account['id']}", headers=_manager(other["id"])).status_code == 404
    # And an account id of another Betrieb under one's own dealership path.
    own = client.get(f"/v1/dealerships/{other['id']}/bank-accounts/{account['id']}", headers=_manager(other["id"]))
    assert own.status_code == 404


def test_platform_staff_read_and_write_bank_accounts(client):
    dealership = _create_dealership(client)
    headers = _bearer(_token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealership["id"])))
    response = client.post(f"/v1/dealerships/{dealership['id']}/bank-accounts", json=_account_payload(), headers=headers)
    assert response.status_code == 201, response.text


# --- settings: the shipped defaults -----------------------------------------------


def test_a_new_betrieb_reads_the_shipped_fiscal_defaults_and_no_vat_method(client):
    dealership = _create_dealership(client)
    response = client.get(f"/v1/dealerships/{dealership['id']}/settings", headers=_role(dealership["id"], AccessRole.SALES))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["currency"] == "CHF"
    assert body["cashRounding"] == "0.05"
    assert body["defaultPaymentTermsDays"] == 30
    assert body["fiscalYearStart"] == "01-01"
    assert body["accountingSystem"] is None and body["mandant"] is None
    assert body["chartOfAccountsVariant"] == "kmu"
    assert body["periodLockPolicy"] == "month_end_close"
    assert body["version"] == 1
    assert "vatMethod" not in body and "vatRate" not in body  # D-A-27; the VAT rate stays on the dealership


def test_the_settings_model_has_no_vat_method_column():
    assert not any("vat" in column.name for column in DealershipSettings.__table__.columns)


def test_create_dealership_writes_the_settings_row_in_the_same_transaction(client, db_session):
    dealership = _create_dealership(client)
    row = db_session.scalar(sa.select(DealershipSettings).where(DealershipSettings.dealership_id == uuid.UUID(dealership["id"])))
    assert row is not None and row.currency == "CHF"


def test_a_dealership_row_without_settings_reads_them_created_on_demand(client, db_session):
    """Rows inserted around the service (fixtures, the smoke seed) have no
    settings row; the first read creates it with the shipped values."""

    group = DealerGroup(name="G")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage AG",
        franchise_type=FranchiseType.INDEPENDENT,
        phone="+41441234567",
        uid=valid_uid(),
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
    )
    db_session.add(dealership)
    db_session.commit()
    response = client.get(f"/v1/dealerships/{dealership.id}/settings", headers=_manager(str(dealership.id)))
    assert response.status_code == 200, response.text
    assert response.json()["cashRounding"] == "0.05"
    assert db_session.scalar(sa.select(sa.func.count()).select_from(DealershipSettings)) == 1


def test_the_manager_changes_settings_with_if_match_and_the_change_is_audited(client):
    dealership = _create_dealership(client)
    headers = _manager(dealership["id"])
    url = f"/v1/dealerships/{dealership['id']}/settings"

    assert client.patch(url, json={"cashRounding": "0.10"}, headers=headers).status_code == 400
    assert client.patch(url, json={"cashRounding": "0.10"}, headers={**headers, "If-Match": "3"}).status_code == 409

    response = client.patch(
        url,
        json={"cashRounding": "0.10", "defaultPaymentTermsDays": 10, "accountingSystem": "Abacus", "mandant": "4711"},
        headers={**headers, "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cashRounding"] == "0.10" and body["defaultPaymentTermsDays"] == 10 and body["version"] == 2

    event = next(e for e in _audit(client, dealership["id"]) if e["entityType"] == "dealership_settings")
    assert event["before"] == {"cash_rounding": "0.05", "default_payment_terms_days": 30, "accounting_system": None, "mandant": None}
    assert event["after"] == {"cash_rounding": "0.10", "default_payment_terms_days": 10, "accounting_system": "Abacus", "mandant": "4711"}


@pytest.mark.parametrize(
    "payload",
    [
        {"cashRounding": "0.07"},
        {"fiscalYearStart": "02-30"},
        {"fiscalYearStart": "02-29"},
        {"fiscalYearStart": "1.1."},
        {"currency": "chf"},
        {"defaultPaymentTermsDays": 400},
        {"periodLockPolicy": "never"},
        {"currency": None},
    ],
)
def test_settings_values_outside_the_rules_are_422(client, payload):
    dealership = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}/settings",
        json=payload,
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text


def test_any_role_reads_settings_but_only_the_manager_writes(client):
    dealership = _create_dealership(client)
    url = f"/v1/dealerships/{dealership['id']}/settings"
    sales = _role(dealership["id"], AccessRole.SALES)
    assert client.get(url, headers=sales).status_code == 200
    refused = client.patch(url, json={"cashRounding": "0.10"}, headers={**sales, "If-Match": "1"})
    assert refused.status_code == 403
    other = _create_dealership(client)
    assert client.get(url, headers=_manager(other["id"])).status_code == 404


# --- legal identity: confirmation, reference lists, the rest of the master data ------


def test_changing_the_legal_identity_without_confirmation_is_a_422(client):
    dealership = _create_dealership(client)
    headers = {**_manager(dealership["id"]), "If-Match": "1"}
    for payload in ({"legalName": "Neue Garage AG"}, {"uid": VALID_UID}, {"vatLiable": True}):
        response = client.patch(f"/v1/dealerships/{dealership['id']}", json=payload, headers=headers)
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"]["details"]["reason"] == "confirmation_required"
    assert client.get(f"/v1/dealerships/{dealership['id']}", headers=headers).json()["version"] == 1

    # Clearing a VAT number that is set is a change too.
    liable = _create_dealership(client, uid=VALID_UID, vatNumber=f"{VALID_UID} MWST", vatLiable=True)
    cleared = client.patch(
        f"/v1/dealerships/{liable['id']}", json={"vatNumber": None}, headers={**_manager(liable["id"]), "If-Match": "1"}
    )
    assert cleared.status_code == 422, cleared.text
    assert cleared.json()["error"]["details"]["fields"] == ["vat_number"]


def test_a_legal_identity_field_sent_unchanged_needs_no_confirmation(client):
    dealership = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"legalName": dealership["legalName"], "tradeName": "Garage Muster"},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text


def test_a_confirmed_change_lands_with_its_note_as_the_audit_reason(client):
    dealership = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"legalName": "Neue Garage AG", "confirmation": True, "auditNote": "Umfirmierung per HR-Eintrag"},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    event = next(e for e in _audit(client, dealership["id"]) if e["action"] == "update")
    assert event["before"] == {"legal_name": dealership["legalName"]}
    assert event["after"] == {"legal_name": "Neue Garage AG"}
    assert event["reason"] == "Umfirmierung per HR-Eintrag"


def test_every_changed_master_data_field_is_audited_with_before_and_after(client):
    dealership = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={
            "tradeName": "Garage Muster",
            "hrNumber": "CH-020.3.012.345-6",
            "commercialRegisterOffice": "ZH",
            "astraOperatorNumber": "ZH-0815",
            "vatRate": "8.10",
            "address": {**VALID_ADDRESS, "houseNumber": "2"},
        },
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    event = next(e for e in _audit(client, dealership["id"]) if e["action"] == "update")
    assert event["before"]["trade_name"] is None and event["after"]["trade_name"] == "Garage Muster"
    assert event["after"]["hr_number"] == "CH-020.3.012.345-6"
    assert event["after"]["vat_rate"] == "8.10"
    assert event["before"]["address"]["house_number"] == "1" and event["after"]["address"]["house_number"] == "2"
    assert event["reason"] is None


def test_legal_form_and_associations_come_from_the_seeded_lists(client, db_session):
    seed_business_reference_lists(db_session)
    dealership = _create_dealership(client)
    headers = {**_manager(dealership["id"]), "If-Match": "1"}

    unknown = client.patch(f"/v1/dealerships/{dealership['id']}", json={"legalForm": "llc"}, headers=headers)
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["error"]["details"]["reason"] == "unknown_reference_code"

    unknown_association = client.patch(
        f"/v1/dealerships/{dealership['id']}", json={"associationMemberships": ["agvs_upsa", "adac"]}, headers=headers
    )
    assert unknown_association.status_code == 422, unknown_association.text

    twice = client.patch(
        f"/v1/dealerships/{dealership['id']}", json={"associationMemberships": ["agvs_upsa", "agvs_upsa"]}, headers=headers
    )
    assert twice.status_code == 422

    ok = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"legalForm": "gmbh", "associationMemberships": ["agvs_upsa", "auto_schweiz"]},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["legalForm"] == "gmbh"
    assert ok.json()["associationMemberships"] == ["agvs_upsa", "auto_schweiz"]


def test_a_missing_business_list_is_a_deployment_fault_not_a_422(client):
    dealership = _create_dealership(client)
    with pytest.raises(RuntimeError, match="legal_form"):
        client.patch(
            f"/v1/dealerships/{dealership['id']}",
            json={"legalForm": "gmbh"},
            headers={**_manager(dealership["id"]), "If-Match": "1"},
        )


def test_the_haendlerschild_list_round_trips_with_canton_and_expiry(client):
    dealership = _create_dealership(client)
    headers = {**_manager(dealership["id"]), "If-Match": "1"}
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={
            "collectiveVehicleLicences": [
                {"plateNumber": "zh 12345 u", "canton": "ZH", "expiresOn": "2027-03-31"},
                {"plateNumber": "ZH 12346 U", "canton": "ZH"},
            ]
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["collectiveVehicleLicences"] == [
        {"plateNumber": "ZH 12345 U", "canton": "ZH", "expiresOn": "2027-03-31"},
        {"plateNumber": "ZH 12346 U", "canton": "ZH", "expiresOn": None},
    ]
    bad_canton = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"collectiveVehicleLicences": [{"plateNumber": "ZH 1 U", "canton": "XX"}]},
        headers={**headers, "If-Match": "2"},
    )
    assert bad_canton.status_code == 422
    cleared = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"collectiveVehicleLicences": None},
        headers={**headers, "If-Match": "2"},
    )
    assert cleared.status_code == 200 and cleared.json()["collectiveVehicleLicences"] is None


def test_master_data_write_is_the_managers_not_a_roles(client):
    dealership = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{dealership['id']}",
        json={"tradeName": "Garage Muster"},
        headers={**_role(dealership["id"], AccessRole.SALES), "If-Match": "1"},
    )
    assert response.status_code == 403


# --- the migration ------------------------------------------------------------------


@pytest.fixture(scope="module")
def migration():
    path = _MIGRATION / "e1f7a3c9b2d4_kan203_business_master_data.py"
    spec = importlib.util.spec_from_file_location("kan203_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chains_from_kan202_and_seeds_the_business_lists(migration):
    assert migration.down_revision == "b4e8d2f6a1c3"
    assert migration.BUSINESS_LISTS is BUSINESS_LISTS
    assert set(BUSINESS_LISTS) == {"legal_form", "association_membership"}
    for rows in BUSINESS_LISTS.values():
        codes = [row[0] for row in rows]
        assert len(codes) == len(set(codes))
        assert all(all(label for label in row[1:]) for row in rows), "every row carries DE/FR/IT/EN"
    assert "ag" in {r[0] for r in BUSINESS_LISTS["legal_form"]}
    assert "agvs_upsa" in {r[0] for r in BUSINESS_LISTS["association_membership"]}


def test_backfill_gives_every_existing_dealership_the_shipped_defaults_once(client, db_session, migration):
    """The INSERT … SELECT the migration runs, on a Betrieb that has no
    settings row: one row with the shipped values; a second run adds none."""

    dealership = _create_dealership(client)
    db_session.execute(sa.delete(DealershipSettings))
    db_session.commit()
    db_session.execute(sa.text(migration.BACKFILL_SQL))
    db_session.execute(sa.text(migration.BACKFILL_SQL))
    db_session.commit()
    rows = db_session.scalars(sa.select(DealershipSettings)).all()
    assert [str(r.dealership_id) for r in rows] == [dealership["id"]]
    row = rows[0]
    assert (row.currency, str(row.cash_rounding), row.default_payment_terms_days, row.fiscal_year_start) == (
        "CHF",
        "0.05",
        30,
        "01-01",
    )
    assert row.chart_of_accounts_variant.value == "kmu" and row.period_lock_policy.value == "month_end_close"
    assert row.accounting_system is None and row.mandant is None and row.version == 1

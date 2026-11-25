"""KAN-819: the contact search finds a business contact by its UID.

`contact.tax_id` is an `EncryptedString`, which SQL cannot compare; the search
goes through the keyed blind index `tax_id_search_key` (app/core/blind_index.py),
which the model writes on every assignment of `tax_id`.
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.auth import AccessRole, create_access_token
from app.core.blind_index import canonical_uid, uid_search_key
from app.core.config import get_settings
from tests.dealership_support import valid_uid
from tests.token_support import dealer_group_of

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "customer"
    / "9e4b7c2d1a58_kan819_contact_tax_id_search_key.py"
)

_ADDRESS = {
    "street": "Bahnhofstrasse",
    "houseNumber": "1",
    "postalCode": "8001",
    "locality": "Zürich",
    "canton": "ZH",
}


def _token(role: AccessRole | None, tenant_id: uuid.UUID, *, is_dealer_manager: bool = False) -> str:
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({role}) if role is not None else frozenset(),
        is_dealer_manager=is_dealer_manager,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _dealer(client) -> uuid.UUID:
    admin = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
    )
    response = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Garage Musterbetrieb AG",
            "franchiseType": "independent",
            "address": _ADDRESS,
            "phone": "+41441234567",
            "uid": valid_uid(),
        },
        headers=_bearer(admin),
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _business(client, dealer_id: uuid.UUID, *, company: str, tax_id: str | None) -> dict:
    payload = {
        "contactType": "business",
        "companyName": company,
        "language": "de",
        "emails": [{"emailType": "work", "emailAddress": f"{uuid.uuid4().hex[:8]}@example.ch"}],
    }
    if tax_id is not None:
        payload["taxId"] = tax_id
    response = client.post(
        "/v1/contacts", json=payload, headers=_bearer(_token(None, dealer_id, is_dealer_manager=True))
    )
    assert response.status_code == 201, response.text
    return response.json()


def _search(client, dealer_id: uuid.UUID, q: str) -> list[str]:
    response = client.get("/v1/contacts", params={"q": q}, headers=_bearer(_token(AccessRole.SALES, dealer_id)))
    assert response.status_code == 200, response.text
    return [item["companyName"] for item in response.json()["items"]]


def _spellings(uid: str) -> list[str]:
    digits = uid[4:].replace(".", "")
    return [
        uid,
        f"CHE{digits}",
        uid.lower(),
        f"CHE {digits[:3]} {digits[3:6]} {digits[6:]}",
        f"{uid} MWST",
        f"{uid}TVA",
        f"{uid} iva",
        f"  {uid}  ",
        f"{digits[:3]}.{digits[3:6]}.{digits[6:]}",
    ]


def test_a_business_contact_is_found_by_its_uid_in_every_common_spelling(client):
    dealer_id = _dealer(client)
    uid = valid_uid()
    _business(client, dealer_id, company="Lieferant Keller AG", tax_id=uid)
    _business(client, dealer_id, company="Andere Firma AG", tax_id=valid_uid())

    for typed in _spellings(uid):
        assert _search(client, dealer_id, typed) == ["Lieferant Keller AG"], typed


def test_a_part_of_a_uid_finds_nothing_by_it(client):
    dealer_id = _dealer(client)
    uid = valid_uid()
    _business(client, dealer_id, company="Lieferant Keller AG", tax_id=uid)

    assert _search(client, dealer_id, uid[:11]) == []


def test_the_search_key_follows_every_change_of_the_uid(client, db_session):
    dealer_id = _dealer(client)
    old_uid, new_uid = valid_uid(), valid_uid()
    created = _business(client, dealer_id, company="Wechsel AG", tax_id=old_uid)
    manager = _bearer(_token(None, dealer_id, is_dealer_manager=True))

    changed = client.patch(
        f"/v1/contacts/{created['id']}",
        json={"taxId": new_uid},
        headers={**manager, "If-Match": str(created["version"])},
    )
    assert changed.status_code == 200, changed.text
    assert _search(client, dealer_id, old_uid) == []
    assert _search(client, dealer_id, new_uid) == ["Wechsel AG"]

    cleared = client.patch(
        f"/v1/contacts/{created['id']}",
        json={"taxId": None},
        headers={**manager, "If-Match": str(changed.json()["version"])},
    )
    assert cleared.status_code == 200, cleared.text
    assert _search(client, dealer_id, new_uid) == []
    stored = db_session.execute(
        text("SELECT tax_id, tax_id_search_key FROM contact WHERE id = :id"), {"id": created["id"]}
    ).one()
    assert stored == (None, None)


def test_the_column_holds_no_uid_and_the_uid_stays_encrypted(client, db_session):
    dealer_id = _dealer(client)
    uid = valid_uid()
    created = _business(client, dealer_id, company="Verschlüsselt AG", tax_id=uid)

    raw_tax_id, key = db_session.execute(
        text("SELECT tax_id, tax_id_search_key FROM contact WHERE id = :id"), {"id": created["id"]}
    ).one()
    digits = uid[4:].replace(".", "")
    assert raw_tax_id.startswith("gAAAAA")
    assert key == uid_search_key(uid)
    assert len(key) == 64
    assert digits not in key and uid not in key


def test_another_group_does_not_find_the_contact_by_its_uid(client):
    dealer_id = _dealer(client)
    uid = valid_uid()
    _business(client, dealer_id, company="Fremd AG", tax_id=uid)

    assert _search(client, _dealer(client), uid) == []


def test_the_duplicate_check_names_the_same_uid_an_exact_match(client):
    dealer_id = _dealer(client)
    uid = valid_uid()
    created = _business(client, dealer_id, company="Doppel AG", tax_id=uid)

    response = client.get(
        "/v1/contacts/duplicate-check",
        params={"q": f"CHE{uid[4:].replace('.', '')}"},
        headers=_bearer(_token(AccessRole.SALES, dealer_id)),
    )
    assert response.status_code == 200, response.text
    candidates = response.json()["items"]
    assert [(c["id"], c["match"]) for c in candidates] == [(created["id"], "exact")]


def test_neither_the_create_nor_the_search_logs_the_uid(client, caplog):
    dealer_id = _dealer(client)
    uid = valid_uid()
    digits = uid[4:].replace(".", "")
    with caplog.at_level(logging.DEBUG):
        _business(client, dealer_id, company="Still AG", tax_id=uid)
        assert _search(client, dealer_id, uid) == ["Still AG"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert digits not in logged
    assert uid_search_key(uid) not in logged


@pytest.mark.parametrize("text_", ["", "Keller", "CHE-123.456", "CHE-123.456.7890", "CHE-１２３.456.789", "079 123 45 67"])
def test_text_that_is_no_whole_uid_has_no_search_key(text_):
    assert canonical_uid(text_) is None
    assert uid_search_key(text_) is None


def _migration():
    spec = importlib.util.spec_from_file_location("kan819_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("seed", range(5))
def test_the_migrations_frozen_rule_keys_like_the_application(seed):
    migration = _migration()
    key = get_settings().tax_id_encryption_key
    uid = valid_uid(seed)
    for typed in [*_spellings(uid), "Keller", "CHE-123.456"]:
        assert migration.search_key(typed, key) == uid_search_key(typed), typed

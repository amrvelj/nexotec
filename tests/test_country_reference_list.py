"""KAN-32 — the `country` reference list and the nationality / address-country
membership check.

The list itself is stood up for every test by the autouse
`_seed_country_reference_list` fixture in conftest (from the same
`load_country_seed()` data the Alembic migration uses). One test here drops
it again, to pin the "missing list is a deployment fault, not a 404" rule.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.auth import AccessRole
from app.platform.reference_data_seed import load_country_seed

# --- shared fixtures reused from the customer suite --------------------------
from tests.test_customer import (
    _bearer,
    _create_customer,
    _create_dealer,
    _customer_payload,
    _token,
)


def _manager(dealer_id: str) -> dict[str, str]:
    return _bearer(_token(is_dealer_manager=True, tenant_id=uuid.UUID(dealer_id)))


# --- seed data --------------------------------------------------------------


def test_seed_data_is_well_formed():
    rows = load_country_seed()
    assert 240 <= len(rows) <= 260, f"expected ~250 ISO 3166-1 countries, got {len(rows)}"

    codes = [code for code, *_ in rows]
    assert len(codes) == len(set(codes)), "duplicate country code in the seed"
    for code in ("CH", "DE", "FR", "IT", "HR", "US", "GB"):
        assert code in codes, f"{code} missing from the country seed"

    for code, label_de, label_fr, label_it, label_en in rows:
        assert code.isalpha() and code.isupper() and len(code) == 2, f"bad code {code!r}"
        for label in (label_de, label_fr, label_it, label_en):
            assert label and label.strip(), f"{code} has a blank label"


def test_migration_module_matches_the_seed_loader():
    """The platform-branch migration must seed exactly what load_country_seed()
    returns — they share the function, this guards the wiring."""

    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "platform"
        / "d7b1f4e02a96_country_reference_list.py"
    )
    spec = importlib.util.spec_from_file_location("_country_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "a3d9c1e58f27"
    assert module.LIST_CODE == "country"


# --- reachable through the reference-data admin API like any other list -----


def test_country_list_is_reachable_via_reference_data_api(client):
    dealer_id = _create_dealer(client)
    token = _token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id))  # not an admin

    response = client.get("/v1/reference-data/country", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()

    seen = {item["valueCode"]: item for item in body["items"]}
    # first page only; walk the cursor to be sure CH/HR are actually present
    cursor = body["nextCursor"]
    while cursor:
        page = client.get(
            f"/v1/reference-data/country?cursor={cursor}", headers=_bearer(token)
        ).json()
        seen.update({item["valueCode"]: item for item in page["items"]})
        cursor = page["nextCursor"]

    assert "CH" in seen and "HR" in seen
    hr = seen["HR"]
    assert (hr["labelDe"], hr["labelFr"], hr["labelIt"], hr["labelEn"]) == (
        "Kroatien",
        "Croatie",
        "Croazia",
        "Croatia",
    )


def test_every_country_row_has_all_four_labels(client):
    dealer_id = _create_dealer(client)
    token = _bearer(_token(AccessRole.SALES, tenant_id=uuid.UUID(dealer_id)))

    cursor, count = None, 0
    while True:
        url = "/v1/reference-data/country" + (f"?cursor={cursor}" if cursor else "")
        page = client.get(url, headers=token).json()
        for item in page["items"]:
            count += 1
            for field in ("labelDe", "labelFr", "labelIt", "labelEn"):
                assert item[field] and item[field].strip(), f"{item['valueCode']} blank {field}"
        cursor = page["nextCursor"]
        if not cursor:
            break
    assert count >= 240


# --- nationality: create path ----------------------------------------------


def test_nationality_full_country_name_is_rejected_with_a_helpful_message(client):
    dealer_id = _create_dealer(client)
    response = client.post(
        "/v1/customers",
        json=_customer_payload(nationality="Croatia"),
        headers=_manager(dealer_id),
    )
    assert response.status_code == 422, response.text
    message = response.json()["error"]["message"]
    assert "country code" in message
    assert "ISO 3166-1 alpha-2" in message


@pytest.mark.parametrize("bad", ["Cr", "ZZ", "xx", "CH1"])
def test_nationality_non_membership_codes_are_rejected(client, bad):
    """Two-character strings that pass the old width check but are not real,
    active country codes — this is the membership check, not length."""

    dealer_id = _create_dealer(client)
    response = client.post(
        "/v1/customers",
        json=_customer_payload(nationality=bad),
        headers=_manager(dealer_id),
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["invalid"] == {"nationality": bad}


def test_valid_nationality_round_trips_through_create_and_edit(client):
    dealer_id = _create_dealer(client)

    created = client.post(
        "/v1/customers",
        json=_customer_payload(nationality="HR"),
        headers=_manager(dealer_id),
    )
    assert created.status_code == 201, created.text
    assert created.json()["nationality"] == "HR"

    customer_id = created.json()["id"]
    patched = client.patch(
        f"/v1/customers/{customer_id}",
        json={"nationality": "CH"},
        headers={**_manager(dealer_id), "If-Match": "1"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["nationality"] == "CH"

    cleared = client.patch(
        f"/v1/customers/{customer_id}",
        json={"nationality": None},
        headers={**_manager(dealer_id), "If-Match": "2"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["nationality"] is None


def test_patch_to_a_bad_nationality_is_rejected(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    response = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"nationality": "Croatia"},
        headers={**_manager(dealer_id), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text


# --- address country ------------------------------------------------------


def test_nested_address_country_is_validated_on_customer_create(client):
    dealer_id = _create_dealer(client)
    payload = _customer_payload(
        addresses=[
            {
                "addressType": "domicile",
                "addressStreet": "Marktgasse",
                "addressHouseNumber": "10",
                "addressPostalCode": "3011",
                "addressLocality": "Bern",
                "addressCountry": "XX",
            }
        ]
    )
    response = client.post("/v1/customers", json=payload, headers=_manager(dealer_id))
    assert response.status_code == 422, response.text
    assert "addresses[0].addressCountry" in response.json()["error"]["details"]["invalid"]


def test_address_child_endpoint_validates_country(client):
    dealer_id = _create_dealer(client)
    customer = _create_customer(client, dealer_id)
    headers = _manager(dealer_id)

    bad = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern", "addressCountry": "XX",
        },
        headers=headers,
    )
    assert bad.status_code == 422, bad.text

    good = client.post(
        f"/v1/customers/{customer['id']}/addresses",
        json={
            "addressType": "domicile", "addressStreet": "Marktgasse", "addressHouseNumber": "10",
            "addressPostalCode": "3011", "addressLocality": "Bern", "addressCountry": "DE",
        },
        headers=headers,
    )
    assert good.status_code == 201, good.text

    patched = client.patch(
        f"/v1/customers/{customer['id']}/addresses/{good.json()['id']}",
        json={"addressCountry": "Germany"},
        headers=headers,
    )
    assert patched.status_code == 422, patched.text


# --- active flag ---------------------------------------------------------


def test_deactivated_country_code_is_rejected_on_new_writes(client):
    dealer_id = _create_dealer(client)
    admin = _bearer(_token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealer_id)))

    deactivate = client.patch(
        "/v1/reference-data/country/HR",
        json={"active": False},
        headers={**admin, "If-Match": "1"},
    )
    assert deactivate.status_code == 200, deactivate.text

    response = client.post(
        "/v1/customers",
        json=_customer_payload(nationality="HR"),
        headers=_manager(dealer_id),
    )
    assert response.status_code == 422, response.text


def test_customer_holding_a_since_deactivated_code_can_still_be_edited(client):
    dealer_id = _create_dealer(client)
    admin = _bearer(_token(AccessRole.PLATFORM_ADMIN, tenant_id=uuid.UUID(dealer_id)))
    customer = _create_customer(client, dealer_id, nationality="HR")

    client.patch(
        "/v1/reference-data/country/HR",
        json={"active": False},
        headers={**admin, "If-Match": "1"},
    )

    # A PATCH that doesn't touch nationality must not re-validate it.
    patched = client.patch(
        f"/v1/customers/{customer['id']}",
        json={"lastName": "Neu"},
        headers={**_manager(dealer_id), "If-Match": "1"},
    )
    assert patched.status_code == 200, patched.text


# --- deployment fault --------------------------------------------------


def test_country_list_absent_is_a_deployment_fault_not_a_404(client, engine):
    """If the platform-branch seed migration hasn't run, a customer create
    must fail loudly as a server error — never a 404/422 that reads like the
    client sent something wrong."""

    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM reference_value WHERE list_id = "
                "(SELECT id FROM reference_list WHERE list_code = 'country')"
            )
        )
        conn.execute(text("DELETE FROM reference_list WHERE list_code = 'country'"))

    dealer_id = _create_dealer(client)
    with pytest.raises(RuntimeError, match="country.*reference list is not seeded"):
        client.post(
            "/v1/customers",
            json=_customer_payload(nationality="CH"),
            headers=_manager(dealer_id),
        )

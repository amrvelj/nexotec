"""KAN-775: platform's anchored patterns judge the whole value, and a Betrieb whose postal code
the old rule let through with a trailing newline still reads.

Until KAN-775 these patterns were applied with `re.match(r"^…$")`, whose `$` also matches before
one final newline: «8001\\n» was a Swiss postal code, «#7C3AED\\n» a brand colour, «R-\\n{counter:4}»
… a number pattern's literal part. Each is now refused. `DealershipAddress` is also the read shape,
so the newline the old rule accepted is dropped on read (`models/dealership.py::_stored_postal_code`);
a location PATCH that re-judges the address block reads its stored code the same way.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.auth import AccessRole
from app.core.errors import UnprocessableEntityError
from app.platform.organisation.schemas.branding import _hex
from app.platform.organisation.schemas.dealership_settings import _validate_fiscal_year_start
from app.platform.organisation.services.brand_colors import relative_luminance
from app.platform.organisation.services.number_range import parse_pattern
from tests.platform.test_dealership import VALID_ADDRESS, _bearer, _create_dealership, _dealership_payload, _token


def test_a_dealership_postal_code_with_a_trailing_newline_is_refused(client):
    payload = _dealership_payload(address={**VALID_ADDRESS, "postalCode": "8001\n"})
    response = client.post("/v1/dealerships", json=payload, headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)))
    assert response.status_code == 422, response.text


def test_a_betrieb_stored_with_the_old_rules_newline_still_reads(client, db_session):
    body = _create_dealership(
        client,
        correspondenceAddress={**VALID_ADDRESS, "postalCode": "3000", "locality": "Bern", "canton": "BE"},
    )
    db_session.execute(
        text(
            "update dealership set address_postal_code = :seat, correspondence_address_postal_code = :other"
            " where id = :id"
        ),
        {"seat": "8001\n", "other": "3000\n", "id": body["id"]},
    )
    db_session.commit()

    response = client.get(f"/v1/dealerships/{body['id']}", headers=_bearer(_token(AccessRole.PLATFORM_ADMIN)))

    assert response.status_code == 200, response.text
    assert response.json()["address"]["postalCode"] == "8001"
    assert response.json()["correspondenceAddress"]["postalCode"] == "3000"


def test_a_brand_colour_with_a_trailing_newline_is_refused():
    assert _hex("#7c3aed") == "#7C3AED"
    with pytest.raises(ValueError):
        _hex("#7C3AED\n")
    with pytest.raises(ValueError):
        relative_luminance("#7C3AED\n")


def test_a_number_pattern_with_a_newline_in_its_literal_part_is_refused():
    assert parse_pattern("R-{counter:4}").counter_digits == 4
    with pytest.raises(UnprocessableEntityError) as refused:
        parse_pattern("R-{counter:4}\n")
    assert refused.value.details == {"reason": "number_pattern_invalid", "problem": "character"}


def test_a_counter_token_with_a_newline_is_refused():
    """`_COUNTER_RE` judges the token's inside: «{counter:4\n}» was a counter of four digits."""

    with pytest.raises(UnprocessableEntityError) as refused:
        parse_pattern("R-{counter:4\n}")
    assert refused.value.details == {"reason": "number_pattern_invalid", "problem": "unknown_token"}


def test_a_location_stored_with_the_old_rules_newline_saves_another_address_change(client, db_session):
    """A PATCH touching the address block re-judges the stored postal code: the newline the
    old rule let through is dropped (and saved clean); one the PATCH sends is refused."""

    dealership = _create_dealership(client)
    manager = _bearer(_token(tenant_id=uuid.UUID(dealership["id"]), is_dealer_manager=True))
    url = f"/v1/dealerships/{dealership['id']}/locations"
    [location] = client.get(url, headers=manager).json()["items"]
    db_session.execute(
        text("update location set address_postal_code = :code where id = :id"), {"code": "8001\n", "id": location["id"]}
    )
    db_session.commit()

    refused = client.patch(
        f"{url}/{location['id']}",
        json={"addressPostalCode": "8002\n"},
        headers={**manager, "If-Match": str(location["version"])},
    )
    assert refused.status_code == 422, refused.text

    saved = client.patch(
        f"{url}/{location['id']}",
        json={"addressStreet": "Löwenstrasse"},
        headers={**manager, "If-Match": str(location["version"])},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["addressPostalCode"] == "8001"


def test_a_fiscal_year_start_with_a_trailing_newline_is_refused():
    assert _validate_fiscal_year_start("04-01") == "04-01"
    with pytest.raises(ValueError):
        _validate_fiscal_year_start("04-01\n")

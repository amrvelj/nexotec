"""KAN-844: `dealership.vat_rate` had no bounds on write. `app.core.money`
(ADR-215) refuses a rate below 0 or of 100 % and more, so a Betrieb that typed
one (810 for 8.10) was stored and then got a 500 on every Sales document. The
dealership PATCH now refuses it at the Business screen: 422
`vat_rate_out_of_range`, the field named."""

import pytest

from app.core.auth import AccessRole
from tests.platform.test_dealership import _bearer, _create_dealership, _token


def _patch_rate(client, value):
    body = _create_dealership(client)
    token = _token(AccessRole.PLATFORM_ADMIN)
    response = client.patch(
        f"/v1/dealerships/{body['id']}", json={"vatRate": value}, headers={**_bearer(token), "If-Match": "1"}
    )
    return body, token, response


@pytest.mark.parametrize("value", ["-0.01", "100", "100.00", "810", "8.105"])
def test_a_vat_rate_outside_what_money_takes_is_refused(client, value):
    body, token, response = _patch_rate(client, value)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"reason": "vat_rate_out_of_range", "field": "vatRate"}
    assert client.get(f"/v1/dealerships/{body['id']}", headers=_bearer(token)).json()["vatRate"] == body["vatRate"]


@pytest.mark.parametrize(("value", "stored"), [("0", "0.00"), ("8.1", "8.10"), ("8.100", "8.10"), ("99.99", "99.99")])
def test_a_vat_rate_within_the_range_is_taken(client, value, stored):
    _, _, response = _patch_rate(client, value)

    assert response.status_code == 200, response.text
    assert response.json()["vatRate"] == stored

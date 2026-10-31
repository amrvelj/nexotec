"""KAN-190 item 4: a discount is never more than the amount it is taken
off. `resolve_discount` returned an amount discount as typed, so CHF 5'000
off a CHF 3'000 line made the line, and the offer's total, negative — and
KAN-10 re-applies a kept discount to a re-frozen line, which can be cheaper
than the one it was given on. A percentage over 100 did the same."""

from decimal import Decimal

import pytest

from app.core.auth import AccessRole
from app.sales.services.pricing import resolve_discount
from tests.test_sales_offer_refinements import _bearer, _token


@pytest.mark.parametrize(
    ("discount_type", "value", "base", "resolved"),
    [
        ("amount", Decimal("500.00"), Decimal("3000.00"), Decimal("500.00")),
        ("amount", Decimal("3000.00"), Decimal("3000.00"), Decimal("3000.00")),
        ("amount", Decimal("5000.00"), Decimal("3000.00"), Decimal("3000.00")),
        ("percent", Decimal(10), Decimal("3000.00"), Decimal("300.00")),
        ("percent", Decimal(150), Decimal("3000.00"), Decimal("3000.00")),
        ("amount", Decimal("100.00"), Decimal("0.00"), Decimal("0.00")),
        (None, None, Decimal("3000.00"), Decimal(0)),
    ],
)
def test_a_discount_is_capped_at_what_it_is_taken_off(discount_type, value, base, resolved):
    assert resolve_discount(discount_type, value, base) == resolved


def test_an_offer_discounted_past_its_price_costs_nothing_never_less(client):
    headers = _bearer(_token(AccessRole.SALES))
    offer = client.post("/v1/sales/offers", headers=headers).json()

    priced = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={
            "vehicleSource": "manual", "vehicleLabel": "VW Golf 8 GTI", "manualVehicleCondition": "new",
            "manualBasePrice": "3000.00", "discountType": "amount", "discountValue": "5000.00",
        },
        headers={**headers, "If-Match": str(offer["version"])},
    )

    assert priced.status_code == 200, priced.text
    body = priced.json()
    assert body["discountAmount"] == "3000.00"
    assert body["grossPrice"] == "0.00"
    # What the seller typed is kept; only what it resolves to is capped.
    assert body["discountValue"] == "5000.00"

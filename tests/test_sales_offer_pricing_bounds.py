"""KAN-482: an offer takes the same bounds as the contract for every
shared pricing field (Anto, 2026-10-28: «no, we do not accept negatives»).

OfferUpdate declared manual_base_price and discount_value with no bound
while ContractUpdate, which runs the same pricing code (ADR-083), had
ge=0: an offer stored discountValue -500.00 (a negative discount silently
raises the price) that the contract refused. The leasing figures, offer
only, had no bound either.
"""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.auth import AccessRole
from app.sales.models.offer import SalesOffer
from app.sales.schemas.contract import ContractUpdate
from app.sales.schemas.offer import OfferUpdate
from tests.test_sales_offer_refinements import _bearer, _token

SHARED_PRICING_FIELDS = ("manual_base_price", "discount_type", "discount_value")


@pytest.mark.parametrize("field", SHARED_PRICING_FIELDS)
def test_offer_and_contract_declare_the_same_bounds_for_a_shared_pricing_field(field):
    offer, contract = OfferUpdate.model_fields[field], ContractUpdate.model_fields[field]
    assert offer.annotation == contract.annotation
    # Pydantic's general metadata compares by identity, so compare its repr.
    assert repr(offer.metadata) == repr(contract.metadata)


@pytest.mark.parametrize(
    "payload",
    [
        {"manual_base_price": Decimal("-1.00")},
        {"discount_value": Decimal("-500.00")},
        {"discount_type": "surcharge"},
        {"discount_value": Decimal("1.005")},
        {"leasing_down_payment": Decimal("-1.00")},
        {"leasing_term_months": -12},
        {"leasing_km_per_year": -15000},
    ],
)
def test_offer_update_refuses_a_negative_or_malformed_pricing_figure(payload):
    with pytest.raises(ValidationError):
        OfferUpdate(**payload)


def test_offer_update_accepts_zero_for_every_pricing_figure():
    OfferUpdate(
        manual_base_price=Decimal("0.00"), discount_type="amount", discount_value=Decimal("0.00"),
        leasing_down_payment=Decimal("0.00"), leasing_term_months=0, leasing_km_per_year=0,
    )


@pytest.mark.parametrize(
    ("body", "column"),
    [
        ({"manualBasePrice": "-1.00"}, "manual_base_price"),
        ({"discountType": "surcharge", "discountValue": "100.00"}, "discount_type"),
        ({"leasingDownPayment": "-1000.00"}, "leasing_down_payment"),
        ({"leasingTermMonths": -12}, "leasing_term_months"),
        ({"leasingKmPerYear": -15000}, "leasing_km_per_year"),
    ],
)
def test_api_offer_patch_with_a_refused_pricing_figure_is_a_422_and_stores_nothing(client, db_session, body, column):
    headers = _bearer(_token(AccessRole.SALES))
    offer = client.post("/v1/sales/offers", headers=headers).json()

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}", json=body, headers={**headers, "If-Match": str(offer["version"])}
    )

    assert response.status_code == 422, response.text
    db_session.expire_all()
    stored = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert getattr(stored, column) is None
    assert stored.version == offer["version"]


def test_api_offer_patch_with_a_negative_discount_is_a_422_and_stores_nothing(client, db_session):
    headers = _bearer(_token(AccessRole.SALES))
    offer = client.post("/v1/sales/offers", headers=headers).json()

    response = client.patch(
        f"/v1/sales/offers/{offer['id']}",
        json={"discountType": "amount", "discountValue": "-500.00"},
        headers={**headers, "If-Match": str(offer["version"])},
    )

    assert response.status_code == 422, response.text
    db_session.expire_all()
    stored = db_session.get(SalesOffer, uuid.UUID(offer["id"]))
    assert stored.discount_value is None and stored.discount_type is None

"""KAN-824 (ADR-215; Anto 2026-11-25, option A): Sales rounds through
`app.core.money`, the one money calculation.

- A percent discount is rounded half away from zero («kaufmännisch»), like
  every other figure in the product. Before, it was Python's default half to
  even, so a discount landing exactly on half a Rappen rounded down: the only
  figures that change (listed in the PR with old and new value).
- The VAT printed on an offer or a contract is the VAT component of the
  gross price as typed (ADR-057), taken from core money: the same figure as
  before for every gross price and every two-decimal rate.
- The totals of the offer and contract fixtures stay as they were.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.core.money import included_vat
from app.sales.schemas.contract import ContractUpdate
from app.sales.schemas.line_item import LineItemAccessoryInput, LineItemsReplaceRequest
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.contract import create_contract, update_contract
from app.sales.services.document import _compute_vat_amount
from app.sales.services.line_items import replace_line_items
from app.sales.services.offer import create_offer, update_offer
from app.sales.services.pricing import resolve_discount
from tests.sales.test_sales_lifecycle_reservation import _contact, _dealership
from tests.sales.test_sales_offer_refinements import _offer_with_vehicle, _stock_item

# --- the half-Rappen cases: the only figures that change -------------------------

# (base, percent, old half-to-even figure, new commercial figure)
_HALF_RAPPEN_CASES = [
    (Decimal("45990.00"), Decimal("0.15"), Decimal("68.98"), Decimal("68.99")),
    (Decimal("10000.50"), Decimal(5), Decimal("500.02"), Decimal("500.03")),
    (Decimal("100.50"), Decimal(5), Decimal("5.02"), Decimal("5.03")),
    (Decimal("2.50"), Decimal(5), Decimal("0.12"), Decimal("0.13")),
]


@pytest.mark.parametrize(("base", "percent", "old", "new"), _HALF_RAPPEN_CASES)
def test_a_half_rappen_percent_discount_rounds_half_away_from_zero(base, percent, old, new):
    # The old figure is what the bare .quantize() gave; pinned so the change is visible.
    assert (base * percent / Decimal(100)).quantize(Decimal("0.01")) == old
    assert resolve_discount("percent", percent, base) == new


def test_a_percent_discount_off_a_tie_is_unchanged():
    """Away from a tie the two roundings agree: every percentage from 0.01 %
    to 30 % in steps of 0.37 % off a spread of bases gives the old figure
    unless the exact value is a half Rappen."""

    for base_cents in range(100_00, 60_000_00, 997_13):
        base = Decimal(base_cents) / 100
        percent = Decimal("0.01")
        while percent <= 30:
            exact = base * percent / Decimal(100)
            old = exact.quantize(Decimal("0.01"))
            is_tie = (exact * 100) % 1 == Decimal("0.5")
            expected = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if is_tie else old
            assert resolve_discount("percent", percent, base) == expected, (base, percent)
            percent += Decimal("0.37")


def test_the_discount_cap_and_the_amount_discount_are_untouched():
    assert resolve_discount("percent", Decimal(150), Decimal("3000.00")) == Decimal("3000.00")
    assert resolve_discount("amount", Decimal("68.985"), Decimal("3000.00")) == Decimal("68.985")
    assert resolve_discount(None, None, Decimal("3000.00")) == Decimal(0)


# --- the VAT on the document: no figure changes ----------------------------------


@pytest.mark.parametrize("rate", [Decimal("8.10"), Decimal("7.70"), Decimal("2.60"), Decimal("3.80"), Decimal("0.00")])
def test_the_document_vat_is_core_moneys_included_vat_and_unchanged(rate):
    for gross_cents in list(range(5_000)) + list(range(1_995, 9_000_000_00, 1_234_567)):
        gross = Decimal(gross_cents) / 100
        before = (gross * rate / (Decimal(100) + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert _compute_vat_amount(gross_price=gross, vat_rate=rate) == before, (gross, rate)
        assert included_vat(gross, rate) == before


def test_included_vat_refuses_what_core_money_refuses():
    with pytest.raises(TypeError):
        included_vat(19.95, Decimal("8.1"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        included_vat(Decimal("19.95"), Decimal("8.125"))


# --- the fixtures' totals, before and after ---------------------------------------


def test_an_offer_fixtures_totals_are_pinned(db_session):
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)  # 38'000 base + LED 800
    offer = _offer_with_vehicle(db_session, dealership.id, item)
    offer = replace_line_items(
        db_session,
        deal=offer,
        data=LineItemsReplaceRequest(
            accessories=[
                LineItemAccessoryInput(
                    code="MATS", label="Fussmatten", unit_price=Decimal("150.10"), quantity=2,
                    discount_type="percent", discount_value=Decimal("2.5"),
                )
            ],
        ),
        actor_id=uuid.uuid4(),
    )
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(discount_type="percent", discount_value=Decimal("7.5")), actor_id=uuid.uuid4(),
    )

    assert offer.list_price == Decimal("38800.00")
    assert offer.accessories_total == Decimal("292.69")  # 300.20 less 7.505 → 7.51 (a tie: was 7.50)
    assert offer.total_before_discount == Decimal("39092.69")
    assert offer.discount_amount == Decimal("2931.95")  # 2'931.95175
    assert offer.gross_price == Decimal("36160.74")
    assert _compute_vat_amount(gross_price=offer.gross_price, vat_rate=Decimal("8.10")) == Decimal("2709.55")


def test_a_contract_fixtures_totals_are_pinned(db_session):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contact = _contact(db_session, group_id)
    contract = create_contract(
        db_session, tenant_id=dealership.id, offer=None, actor_id=uuid.uuid4(), contact_id=contact.id,
        group_id=group_id,
    )

    def edit(**fields):
        return update_contract(
            db_session, contract=contract, group_id=uuid.uuid4(), data=ContractUpdate(**fields), actor_id=uuid.uuid4()
        )

    contract = edit(
        vehicle_source="manual", vehicle_label="Volkswagen Käfer", manual_vehicle_condition="used",
        manual_base_price=Decimal("18000.00"),
    )
    contract = edit(discount_type="percent", discount_value=Decimal("3.35"))

    assert contract.discount_amount == Decimal("603.00")
    assert contract.gross_price == Decimal("17397.00")
    assert contract.payable == Decimal("17397.00")
    assert _compute_vat_amount(gross_price=contract.gross_price, vat_rate=Decimal("8.10")) == Decimal("1303.57")


def test_a_half_rappen_vehicle_discount_on_an_offer_takes_the_commercial_figure(db_session):
    dealership = _dealership(db_session)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(
            vehicle_source="manual", vehicle_label="Škoda Octavia", manual_vehicle_condition="new",
            manual_base_price=Decimal("45990.00"),
        ),
        actor_id=uuid.uuid4(),
    )
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(),
        data=OfferUpdate(discount_type="percent", discount_value=Decimal("0.15")), actor_id=uuid.uuid4(),
    )

    assert offer.discount_amount == Decimal("68.99")  # was 68.98
    assert offer.gross_price == Decimal("45921.01")  # was 45'921.02

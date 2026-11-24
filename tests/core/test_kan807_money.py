"""KAN-807 (ADR-215): the one money calculation in core.

Checks 13 and 47 of Aftersales build slice 1 (section 7), the rules of its
section 3.7, and two properties over seeded random documents: the order of
the lines never changes a total, and a net-typed and a gross-typed price of
the same value give the same total.
"""

import ast
import random
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from app.core.money import (
    MoneyLine,
    PayerTotal,
    Price,
    PriceEntry,
    labour_hours,
    payer_totals,
    round_to,
    to_rappen,
)

D = Decimal
VAT = D("8.1")
CASH = D("0.05")


def net(amount: str, rate: Decimal = VAT) -> Price:
    return Price(D(amount), PriceEntry.NET, rate)


def gross(amount: str, rate: Decimal = VAT) -> Price:
    return Price(D(amount), PriceEntry.GROSS, rate)


def test_check_13_net_vat_rounding_and_total():
    """Net 434.85 plus VAT 35.22 is 470.07; rounding −0.02; total 470.05 (Part A's example)."""

    [total] = payer_totals([MoneyLine(1, net("434.85"), "customer")], cash_rounding=CASH).values()
    assert total == PayerTotal(
        payer="customer",
        net=D("434.85"),
        vat_lines=total.vat_lines,
        vat=D("35.22"),
        total=D("470.07"),
        rounding=D("-0.02"),
        rounded_total=D("470.05"),
    )
    [line] = total.vat_lines
    assert (line.rate, line.net, line.vat) == (D("8.10"), D("434.85"), D("35.22"))


def test_check_13_holds_when_the_sum_comes_from_several_lines():
    lines = [
        MoneyLine(D("2.5"), net("100.00"), "customer"),  # 250.00
        MoneyLine(1, net("184.85"), "customer"),
    ]
    [total] = payer_totals(lines, cash_rounding=CASH).values()
    assert (total.net, total.vat, total.total, total.rounding, total.rounded_total) == (
        D("434.85"),
        D("35.22"),
        D("470.07"),
        D("-0.02"),
        D("470.05"),
    )


def test_check_47_work_units_are_hours_times_the_hourly_rate():
    """With 12 work units per hour, 9 AW at CHF 100.00 per hour are CHF 75.00 net."""

    hours = labour_hours(9, 12)
    assert hours == Fraction(3, 4)
    line = MoneyLine(hours, net("100.00"), "customer")
    assert line.net == 75
    [total] = payer_totals([line], cash_rounding=CASH).values()
    assert total.net == D("75.00")


def test_a_third_of_an_hour_stays_exact_until_the_total():
    # 4 AW at 12 per hour is ⅓ h: never 0.33 h on the way.
    line = MoneyLine(labour_hours(4, 12), net("150.00"), "customer")
    assert line.net == 50
    assert payer_totals([line], cash_rounding=CASH)["customer"].net == D("50.00")


def test_vat_is_charged_once_on_the_sum_not_per_line():
    # Per line: 3 × round(0.405) = 3 × 0.41 = 1.23. Once on the sum: round(1.215) = 1.22.
    lines = [MoneyLine(1, net("5.00"), "customer") for _ in range(3)]
    total = payer_totals(lines, cash_rounding=D("0.01"))["customer"]
    assert (total.net, total.vat) == (D("15.00"), D("1.22"))


def test_the_cash_rounding_never_changes_the_vat():
    total = payer_totals([MoneyLine(1, net("434.85"), "customer")], cash_rounding=D("1.00"))["customer"]
    assert total.vat == D("35.22") and total.total == D("470.07")
    assert total.rounding == D("-0.07") and total.rounded_total == D("470.00")
    assert total.net + total.vat == total.total
    assert total.total + total.rounding == total.rounded_total


def test_rounding_is_commercial_half_away_from_zero():
    assert round_to(D("470.075"), CASH) == D("470.10")
    assert round_to(D("470.0749"), CASH) == D("470.05")
    assert to_rappen(D("0.005")) == D("0.01")
    assert to_rappen(D("-0.005")) == D("-0.01")
    assert round_to(D("-470.075"), CASH) == D("-470.10")


def test_a_step_of_one_rappen_rounds_nothing():
    total = payer_totals([MoneyLine(1, net("434.85"), "customer")], cash_rounding=D("0.01"))["customer"]
    assert total.rounding == D("0.00") and total.rounded_total == total.total


def test_each_payer_gets_its_own_vat_and_its_own_rounding():
    lines = [
        MoneyLine(1, net("434.85"), "customer"),
        MoneyLine(1, net("100.00"), "importer"),
        MoneyLine(1, net("0.03"), "customer"),
    ]
    totals = payer_totals(lines, cash_rounding=CASH)
    assert list(totals) == ["customer", "importer"]
    assert totals["customer"].net == D("434.88") and totals["customer"].vat == D("35.23")
    assert totals["customer"].total == D("470.11") and totals["customer"].rounded_total == D("470.10")
    assert totals["importer"].total == D("108.10") and totals["importer"].rounding == D("0.00")


def test_two_rates_get_two_vat_lines():
    lines = [MoneyLine(1, net("100.00"), "customer"), MoneyLine(1, net("100.00", D("2.6")), "customer")]
    total = payer_totals(lines, cash_rounding=CASH)["customer"]
    assert [(v.rate, v.net, v.vat) for v in total.vat_lines] == [
        (D("2.60"), D("100.00"), D("2.60")),
        (D("8.10"), D("100.00"), D("8.10")),
    ]
    assert total.vat == D("10.70") and total.total == D("210.70")


def test_8_1_and_8_10_are_one_rate():
    lines = [MoneyLine(1, net("5.00", D("8.1")), "c"), MoneyLine(1, net("5.00", D("8.10")), "c")]
    assert len(payer_totals(lines, cash_rounding=CASH)["c"].vat_lines) == 1


def test_a_price_is_kept_as_typed_and_the_other_figure_follows():
    typed_net = net("100.00")
    assert typed_net.shown(PriceEntry.NET) == D("100.00")
    assert typed_net.shown(PriceEntry.GROSS) == D("108.1000")
    typed_gross = gross("100.00")
    assert typed_gross.shown(PriceEntry.GROSS) == D("100.00")
    assert typed_gross.shown(PriceEntry.NET) == D("92.5069")
    assert typed_gross.net * (1 + Fraction(81, 1000)) == 100  # exact, not 92.5069 × 1.081


def test_a_gross_typed_price_totals_to_its_gross():
    total = payer_totals([MoneyLine(1, gross("100.00"), "customer")], cash_rounding=CASH)["customer"]
    assert (total.net, total.vat, total.total) == (D("92.51"), D("7.49"), D("100.00"))


def test_a_unit_price_has_up_to_four_decimals():
    assert Price(D("12.3456"), PriceEntry.NET, VAT).amount == D("12.3456")
    with pytest.raises(ValueError):
        Price(D("12.34567"), PriceEntry.NET, VAT)
    line = MoneyLine(3, net("0.3333"), "customer")
    assert payer_totals([line], cash_rounding=D("0.01"))["customer"].net == D("1.00")


@pytest.mark.parametrize(
    "build",
    [
        lambda: Price(1.5, PriceEntry.NET, VAT),  # type: ignore[arg-type]
        lambda: Price(D("1.50"), PriceEntry.NET, 8.1),  # type: ignore[arg-type]
        lambda: Price(D("1.50"), "net", VAT),  # type: ignore[arg-type]
        lambda: Price(D("NaN"), PriceEntry.NET, VAT),
        lambda: Price(D("1.50"), PriceEntry.NET, D("100")),
        lambda: Price(D("1.50"), PriceEntry.NET, D("-1")),
        lambda: Price(D("1.50"), PriceEntry.NET, D("8.123")),
        lambda: MoneyLine(1.5, net("1.00"), "customer"),  # type: ignore[arg-type]
        lambda: MoneyLine(True, net("1.00"), "customer"),
        lambda: MoneyLine(1, net("1.00"), ""),
        lambda: labour_hours(9, 0),
        lambda: payer_totals([], cash_rounding=0.05),  # type: ignore[arg-type]
        lambda: payer_totals([], cash_rounding=D("0.005")),
        lambda: payer_totals([], cash_rounding=D("0")),
    ],
)
def test_floats_and_impossible_values_are_refused(build):
    with pytest.raises((TypeError, ValueError)):
        build()


def test_no_lines_no_payer():
    assert payer_totals([], cash_rounding=CASH) == {}


# -- properties, over seeded random documents (no new dependency) -----------------------------

_RATES = (D("0"), D("2.6"), D("3.8"), D("8.1"))
_PAYERS = ("customer", "importer", "insurer")


def _random_lines(rng: random.Random) -> list[MoneyLine]:
    lines = []
    for _ in range(rng.randint(1, 12)):
        price = Price(
            D(rng.randint(-50_000, 5_000_000)).scaleb(-4),
            rng.choice(list(PriceEntry)),
            rng.choice(_RATES),
        )
        quantity = rng.choice(
            [D(rng.randint(1, 400)).scaleb(-2), rng.randint(1, 20), labour_hours(rng.randint(1, 60), 12)]
        )
        lines.append(MoneyLine(quantity, price, rng.choice(_PAYERS)))
    return lines


def _figures(totals: dict[str, PayerTotal]) -> dict[str, PayerTotal]:
    return dict(sorted(totals.items()))


@pytest.mark.parametrize("seed", range(200))
def test_the_order_of_the_lines_never_changes_a_total(seed):
    rng = random.Random(seed)
    lines = _random_lines(rng)
    shuffled = lines[:]
    rng.shuffle(shuffled)
    assert _figures(payer_totals(lines, cash_rounding=CASH)) == _figures(payer_totals(shuffled, cash_rounding=CASH))


@pytest.mark.parametrize("seed", range(200))
def test_net_typed_and_gross_typed_prices_of_the_same_value_give_the_same_total(seed):
    rng = random.Random(seed)
    lines = _random_lines(rng)
    as_net, as_gross = [], []
    for line in lines:
        # A gross price that is exactly the net price with its VAT (four decimals or fewer).
        net_amount = line.price.amount if line.price.entered is PriceEntry.NET else D(rng.randint(0, 99_999)).scaleb(-2)
        gross_amount = net_amount * (1 + line.price.vat_rate / 100)
        if gross_amount.as_tuple().exponent < -4:  # type: ignore[operator]
            net_amount = net_amount.quantize(D(1))
            gross_amount = net_amount * (1 + line.price.vat_rate / 100)
        as_net.append(MoneyLine(line.quantity, Price(net_amount, PriceEntry.NET, line.price.vat_rate), line.payer))
        as_gross.append(
            MoneyLine(
                line.quantity,
                Price(gross_amount.normalize() if gross_amount else D(0), PriceEntry.GROSS, line.price.vat_rate),
                line.payer,
            )
        )
    assert payer_totals(as_net, cash_rounding=CASH) == payer_totals(as_gross, cash_rounding=CASH)


@pytest.mark.parametrize("seed", range(200))
def test_every_total_adds_up(seed):
    for total in payer_totals(_random_lines(random.Random(seed)), cash_rounding=CASH).values():
        assert total.net == sum(v.net for v in total.vat_lines)
        assert total.vat == sum(v.vat for v in total.vat_lines)
        assert total.net + total.vat == total.total
        assert total.total + total.rounding == total.rounded_total
        assert abs(total.rounding) <= CASH / 2
        assert (total.rounded_total / CASH) == (total.rounded_total / CASH).to_integral_value()
        for v in total.vat_lines:
            assert v.vat == to_rappen(Fraction(v.net) * Fraction(v.rate) / 100)


def test_the_module_imports_no_context_and_no_io():
    source = Path(__file__).resolve().parents[2] / "app" / "core" / "money.py"
    imported = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "enum", "collections", "decimal", "fractions"}

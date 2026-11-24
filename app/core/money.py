"""One money calculation (KAN-807; ADR-215; Aftersales build slice 1, section 3.7).

An order and its invoice must total the same way, to the Rappen, whichever
module builds them. This module is that one calculation. It is pure: no I/O,
no database, no import from any bounded context, `Decimal` and `Fraction`
only — a float is refused wherever an amount comes in.

**Exact until the end.** Amounts are carried as `Fraction`s, so nothing is
rounded on the way: a gross price turned into its net, hours from work units
(9 AW at 12 per hour is exactly ¾ h), a quantity times a four-decimal unit
price. Rounding happens only in `payer_totals`, in this order (decisions 15,
20, 32 and 34 of the Aftersales PRD):

1. per payer and VAT rate, the **net sum** of the lines, exact;
2. the **VAT** on that exact sum, once; then the net and the VAT are each
   rounded to the Rappen. Taking the VAT on the exact sum keeps a price typed
   gross: CHF 19.95 with VAT totals CHF 19.95 (VAT on the rounded net would
   make it 19.96 for about one gross price in thirteen). The printed VAT is
   within a Rappen of the rate times the printed net;
3. the **total** = net + VAT, to the Rappen;
4. the Betrieb's **cash rounding** (the step the caller passes, e.g. 0.05) on
   the payer's total; the difference is its own line, «Rundung». The VAT is
   never changed by it.

Every rounding is commercial (half away from zero, «kaufmännisch»): 0.005
becomes 0.01, 470.075 becomes 470.10 at a step of 0.05.

**A price is kept as typed** (decision 15): a `Price` holds the amount the
user typed, net or gross, with up to four decimals (decision 23); the other
figure follows exactly from the VAT rate. A net-typed and a gross-typed price
of the same value give the same total.

**Labour** is hours times the hourly rate, whatever unit shows the time
(ruling 183): `labour_hours` turns work units into exact hours. A price per
work unit is never calculated with.

Sales does not use this module yet; it moves to it in a ticket of its own,
with its totals unchanged (ADR-215).
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

RAPPEN = Decimal("0.01")
# A unit price carries up to four decimals (decision 23).
UNIT_PRICE_PLACES = 4

Exact = Decimal | Fraction | int


class PriceEntry(str, enum.Enum):
    """Which figure the user typed; the other one follows."""

    NET = "net"
    GROSS = "gross"


def _exact(value: Exact, what: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (Decimal, Fraction, int)):
        raise TypeError(f"{what} is a Decimal, a Fraction or an int, never {type(value).__name__}.")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError(f"{what} is a finite number.")
    return Fraction(value)


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def round_to(value: Exact, step: Decimal = RAPPEN) -> Decimal:
    """`value` rounded to a multiple of `step`, half away from zero."""

    step_exact = _exact(step, "A rounding step")
    if step_exact <= 0:
        raise ValueError("A rounding step is above zero.")
    if (step_exact / Fraction(RAPPEN)).denominator != 1:
        raise ValueError("A rounding step is a whole number of Rappen.")
    return _round(_exact(value, "An amount"), step_exact)


def _round(exact: Fraction, step_exact: Fraction) -> Decimal:
    steps = exact / step_exact
    whole = int(abs(steps) + Fraction(1, 2))  # half away from zero
    rounded = Fraction(whole if steps >= 0 else -whole) * step_exact
    return Decimal(rounded.numerator) / Decimal(rounded.denominator) if rounded else Decimal(0)


def to_rappen(value: Exact) -> Decimal:
    """`value` to the Rappen, always with two decimals."""

    return round_to(value, RAPPEN).quantize(RAPPEN, rounding=ROUND_HALF_UP)


def _rate(vat_rate: Decimal) -> Fraction:
    if not isinstance(vat_rate, Decimal):
        raise TypeError("A VAT rate is a Decimal percentage, e.g. Decimal('8.1').")
    rate = _exact(vat_rate, "A VAT rate")
    if rate < 0 or rate >= 100 or _places(vat_rate) > 2:
        raise ValueError("A VAT rate is a percentage from 0 to below 100 with at most two decimals.")
    return rate / 100


@dataclasses.dataclass(frozen=True)
class Price:
    """A unit price as the user typed it: net or gross, at a VAT rate in
    percent (8.1 for 8.1 %). The typed amount is kept as typed."""

    amount: Decimal
    entered: PriceEntry
    vat_rate: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("A price is a Decimal.")
        _exact(self.amount, "A price")
        if _places(self.amount) > UNIT_PRICE_PLACES:
            raise ValueError(f"A price has at most {UNIT_PRICE_PLACES} decimals.")
        if not isinstance(self.entered, PriceEntry):
            raise TypeError("A price says whether it was typed net or gross.")
        _rate(self.vat_rate)

    @property
    def net(self) -> Fraction:
        """The unit price without VAT, exact."""

        amount = Fraction(self.amount)
        if self.entered is PriceEntry.NET:
            return amount
        return amount / (1 + _rate(self.vat_rate))

    @property
    def gross(self) -> Fraction:
        """The unit price with VAT, exact."""

        amount = Fraction(self.amount)
        if self.entered is PriceEntry.GROSS:
            return amount
        return amount * (1 + _rate(self.vat_rate))

    def shown(self, figure: PriceEntry) -> Decimal:
        """The net or gross figure as a screen shows it: the typed one as
        typed, the other one to four decimals."""

        if figure is self.entered:
            return self.amount
        value = self.net if figure is PriceEntry.NET else self.gross
        unit = Decimal(1).scaleb(-UNIT_PRICE_PLACES)
        return _round(value, Fraction(unit)).quantize(unit, rounding=ROUND_HALF_UP)


def labour_hours(work_units: Exact, units_per_hour: Exact) -> Fraction:
    """Work units (AW) as hours, exact: 9 AW at 12 per hour is ¾ h (ruling 183)."""

    units = _exact(work_units, "Work units")
    per_hour = _exact(units_per_hour, "Work units per hour")
    if per_hour <= 0:
        raise ValueError("Work units per hour is above zero.")
    return units / per_hour


@dataclasses.dataclass(frozen=True)
class MoneyLine:
    """One line of a document: a quantity (pieces, or hours for labour) at a
    unit price, owed by one payer."""

    quantity: Exact
    price: Price
    payer: str

    def __post_init__(self) -> None:
        _exact(self.quantity, "A quantity")
        if not isinstance(self.payer, str) or not self.payer:
            raise ValueError("A line names its payer.")

    @property
    def net(self) -> Fraction:
        """The line's net amount, exact."""

        return _exact(self.quantity, "A quantity") * self.price.net


@dataclasses.dataclass(frozen=True)
class VatLine:
    """The VAT of one rate for one payer: once, on the exact net sum; both rounded to the Rappen."""

    rate: Decimal
    net: Decimal
    vat: Decimal


@dataclasses.dataclass(frozen=True)
class PayerTotal:
    """What one payer owes: the net sum, one VAT line per rate, the total to
    the Rappen, the cash rounding as its own line («Rundung») and the amount
    due. `net + vat == total` and `total + rounding == rounded_total`."""

    payer: str
    net: Decimal
    vat_lines: tuple[VatLine, ...]
    vat: Decimal
    total: Decimal
    rounding: Decimal
    rounded_total: Decimal


def payer_totals(lines: Iterable[MoneyLine], *, cash_rounding: Decimal) -> dict[str, PayerTotal]:
    """Each payer's total, by payer, in the order the payers first appear.
    The order of the lines never changes a figure."""

    if not isinstance(cash_rounding, Decimal):
        raise TypeError("The cash rounding step is a Decimal, e.g. Decimal('0.05').")
    round_to(0, cash_rounding)  # refuses a step that is not a whole number of Rappen
    by_payer: dict[str, dict[Decimal, Fraction]] = {}
    for line in lines:
        rates = by_payer.setdefault(line.payer, {})
        rate = line.price.vat_rate.quantize(RAPPEN)  # 8.1 and 8.10 are one rate
        rates[rate] = rates.get(rate, Fraction(0)) + line.net
    totals: dict[str, PayerTotal] = {}
    for payer, rates in by_payer.items():
        vat_lines = []
        for rate in sorted(rates):
            exact_net = rates[rate]
            vat_lines.append(VatLine(rate=rate, net=to_rappen(exact_net), vat=to_rappen(exact_net * _rate(rate))))
        net_sum = sum((v.net for v in vat_lines), Decimal("0.00"))
        vat_sum = sum((v.vat for v in vat_lines), Decimal("0.00"))
        total = net_sum + vat_sum
        rounded = round_to(total, cash_rounding).quantize(RAPPEN, rounding=ROUND_HALF_UP)
        totals[payer] = PayerTotal(
            payer=payer,
            net=net_sum,
            vat_lines=tuple(vat_lines),
            vat=vat_sum,
            total=total,
            rounding=rounded - total,
            rounded_total=rounded,
        )
    return totals

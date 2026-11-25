"""The money of a workshop order (KAN-810; ADR-212, ADR-215; checks 12, 47).

Every figure comes from the one money calculation in `app.core.money`:

- a position's **unit price** is a `Price` — the amount as typed, net or
  gross, at the VAT rate it was typed under; the other figure follows
  (decision 15);
- a labour position **by time** is a `MoneyLine` of its hours at the hourly
  price: the hours are exact (`labour_hours`: 9 AW at 12 per hour is ¾ h),
  so the amount is hours × the hourly rate, whatever unit shows the time
  (ruling 183). A **fixed-price** labour position is one line of one at its
  price. A text position carries no money;
- the order's **total** is `payer_totals` per payer (the billing category;
  one in this slice), in two parts: the jobs already accepted, and the jobs
  still open (draft or waiting for the answer). A declined job counts in
  neither.

Pure: no database. The service hands the rows in.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.aftersales.models.service_order import (
    JobAnswerState,
    LabourTimeUnit,
    PositionKind,
    ServiceOrderJob,
    ServiceOrderPosition,
)
from app.core.money import MoneyLine, PayerTotal, Price, PriceEntry, labour_hours, payer_totals, to_rappen

_HOURS_SHOWN = Decimal("0.0001")


def position_hours(position: ServiceOrderPosition) -> Fraction | None:
    """The position's time in hours, exact, from the unit it was typed in."""

    if position.time_quantity is None or position.time_unit is None:
        return None
    if position.time_unit is LabourTimeUnit.WORK_UNITS:
        assert position.work_units_per_hour is not None, "a time in work units keeps its work units per hour"
        return labour_hours(position.time_quantity, position.work_units_per_hour)
    return Fraction(position.time_quantity)


def shown_hours(hours: Fraction | None) -> Decimal | None:
    """Hours as a screen shows them beside a time typed in work units: four decimals."""

    if hours is None:
        return None
    return (Decimal(hours.numerator) / Decimal(hours.denominator)).quantize(_HOURS_SHOWN, rounding=ROUND_HALF_UP)


def position_price(position: ServiceOrderPosition) -> Price | None:
    if position.unit_price is None or position.price_entered is None or position.vat_rate is None:
        return None
    return Price(amount=position.unit_price, entered=position.price_entered, vat_rate=position.vat_rate)


def position_line(position: ServiceOrderPosition, *, payer: str) -> MoneyLine | None:
    """The position as a money line, or None for text and an unpriced position."""

    price = position_price(position)
    if price is None or position.kind is PositionKind.TEXT:
        return None
    if position.kind is PositionKind.LABOUR_TIME:
        hours = position_hours(position)
        if hours is None:
            return None
        return MoneyLine(quantity=hours, price=price, payer=payer)
    return MoneyLine(quantity=1, price=price, payer=payer)


@dataclasses.dataclass(frozen=True)
class LineFigures:
    """A position's line amount net and gross, to the Rappen, for the screen.
    The order's total never adds these up: it is `payer_totals` on the exact lines."""

    net: Decimal
    gross: Decimal


def line_figures(line: MoneyLine | None) -> LineFigures | None:
    if line is None:
        return None
    exact_net = line.net
    exact_gross = Fraction(line.quantity) * line.price.gross
    return LineFigures(net=to_rappen(exact_net), gross=to_rappen(exact_gross))


def unit_price_figures(price: Price | None) -> tuple[Decimal, Decimal] | None:
    """(net, gross) per unit as shown: the typed one as typed, the other to four decimals."""

    if price is None:
        return None
    return price.shown(PriceEntry.NET), price.shown(PriceEntry.GROSS)


_ACCEPTED = frozenset({JobAnswerState.ACCEPTED})
_OPEN = frozenset({JobAnswerState.DRAFT, JobAnswerState.WAITING})


@dataclasses.dataclass(frozen=True)
class OrderTotals:
    """The order's total in two parts (exit criterion 4): what the customer
    has accepted, and what is still open. Each part holds one total per payer."""

    accepted: tuple[PayerTotal, ...]
    open: tuple[PayerTotal, ...]


def order_totals(
    jobs: Iterable[ServiceOrderJob], positions: Iterable[ServiceOrderPosition], *, cash_rounding: Decimal
) -> OrderTotals:
    job_list = list(jobs)
    state_of = {job.id: job.answer_state for job in job_list}
    payer_of = {job.id: job.billing_category.value for job in job_list}
    accepted: list[MoneyLine] = []
    still_open: list[MoneyLine] = []
    for position in positions:
        line = position_line(position, payer=payer_of[position.job_id])
        if line is None:
            continue
        state = state_of[position.job_id]
        if state in _ACCEPTED:
            accepted.append(line)
        elif state in _OPEN:
            still_open.append(line)
    return OrderTotals(
        accepted=tuple(payer_totals(accepted, cash_rounding=cash_rounding).values()),
        open=tuple(payer_totals(still_open, cash_rounding=cash_rounding).values()),
    )

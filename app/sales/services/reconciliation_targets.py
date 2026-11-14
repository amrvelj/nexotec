"""What another context's nightly reconciliation checks against Sales'
`sales_contract` table (rule 10; ADR-107), built here so the mapped
`SalesContract` class never leaves the sales context (KAN-123 step 4,
ADR-162).

Valuation and Stock hold references to a contract (valuation's
`valuation_contract_carry` rows, Stock's `inventory_sales_contract`
projection, `inventory_cancelled_contract` and a reserved car's
`reserved_by_contract_id`). Their `reconciliation.py` asks these questions of
Sales' rows; each is a builder here that reads nothing itself — the
reconciliation run executes it:

- `contract_reference_check` — the id names a contract (an orphan otherwise).
- `signed_contract_carries_valuation` — a signed contract of the Betrieb
  (cancelled since included, ADR-066) carries the valuation as its trade-in.
- `contract_carries_valuation` — the contract with this id, of this Betrieb,
  still carries this valuation, and is pending, signed, or either
  (`CarryState`).
- `contract_changed_since` — the contract was written at or after a moment
  (a call made after its commit may still be on its way).
- `uncarried_contract_carries_valuation` — a pending or signed contract of
  the Betrieb, unchanged since a moment, carries the valuation, and the
  caller's own select over its rows finds none for that contract.
- `contract_differs_from_projection` — Stock's projection row disagrees with
  the contract it mirrors.
- `unprojected_contract_check` — a StateCheck whose rows are contracts,
  created more than a grace ago, for which the caller's select over its own
  table finds no row.

A contract is Betrieb-owned, and an EXISTS embedded in a live query would
answer whether another Betrieb holds a contract, out of
`test_no_cross_context_orm_query.py`'s sight. So these builders are confined
to the reconciliation modules, which only report
(`tests/architecture/test_sales_reconciliation_targets_only_in_reconciliation.py`).
"""

import datetime as dt
import enum
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, String, cast, or_, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.base import utcnow
from app.core.reconciliation import ReferenceCheck, StateCheck
from app.sales.models.contract import ContractStatus, SalesContract

_Column = ColumnElement[Any] | InstrumentedAttribute[Any]


class CarryState(enum.Enum):
    """Which contracts count as carrying a valuation, in Sales' own terms."""

    # Not yet signed and still pending (neither confirmed nor cancelled).
    PENDING = "pending"
    # Signed (`signed_at` set) — cancelled since included (ADR-066).
    SIGNED = "signed"
    # Either of the two.
    PENDING_OR_SIGNED = "pending_or_signed"


def _carrying(state: CarryState) -> ColumnElement[bool]:
    if state is CarryState.PENDING:
        return (SalesContract.status == ContractStatus.PENDING) & SalesContract.signed_at.is_(None)
    if state is CarryState.SIGNED:
        return SalesContract.signed_at.is_not(None)
    return or_(SalesContract.status == ContractStatus.PENDING, SalesContract.signed_at.is_not(None))


def contract_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """The reference `source_fk_column` holds names a contract."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=SalesContract,
        target_id_column=SalesContract.id,
        nullable=nullable,
        source_where=source_where,
    )


def signed_contract_carries_valuation(valuation_id: _Column, *, tenant_id: _Column) -> Exists:
    """A signed contract of `tenant_id`'s Betrieb carries the valuation as its
    trade-in; signed is `signed_at` set, cancelled since included (ADR-066)."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.trade_in_valuation_id == valuation_id,
            _carrying(CarryState.SIGNED),
        )
        .exists()
    )


def contract_carries_valuation(
    contract_id: _Column, *, tenant_id: _Column, valuation_id: _Column, state: CarryState
) -> Exists:
    """The contract `contract_id` — of `tenant_id`'s Betrieb, still carrying
    `valuation_id` as its trade-in — is in `state`."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.id == contract_id,
            SalesContract.tenant_id == tenant_id,
            SalesContract.trade_in_valuation_id == valuation_id,
            _carrying(state),
        )
        .exists()
    )


def contract_changed_since(contract_id: _Column, *, since: dt.datetime) -> Exists:
    """The contract `contract_id` was last written at or after `since`."""

    return select(SalesContract.id).where(SalesContract.id == contract_id, SalesContract.updated_at >= since).exists()


def uncarried_contract_carries_valuation(
    valuation_id: _Column,
    *,
    tenant_id: _Column,
    changed_before: dt.datetime,
    carry: Callable[[InstrumentedAttribute[Any]], Select[Any]],
) -> Exists:
    """A pending or signed contract of `tenant_id`'s Betrieb, last changed
    before `changed_before`, carries `valuation_id` as its trade-in, and
    `carry(contract_id)` — the caller's select over its own rows — finds no
    row for it."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.trade_in_valuation_id == valuation_id,
            _carrying(CarryState.PENDING_OR_SIGNED),
            SalesContract.updated_at < changed_before,
            ~carry(SalesContract.id).exists(),
        )
        .exists()
    )


def contract_differs_from_projection(
    contract_id: _Column,
    *,
    tenant_id: _Column,
    status: _Column,
    invoice_requested_at: _Column,
    stock_item_id: _Column,
    changed_before: dt.datetime,
) -> Exists:
    """The contract `contract_id`, last changed before `changed_before`, says
    something else than a projection row: another Betrieb than `tenant_id`,
    another status, whether the invoice was requested, or — once requested —
    the car it was requested for (KAN-630: Stock's `inventory_sales_contract`)."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.id == contract_id,
            SalesContract.updated_at < changed_before,
            (SalesContract.tenant_id != tenant_id)
            | (cast(SalesContract.status, String) != cast(status, String))
            | (SalesContract.invoice_requested_at.is_(None) != invoice_requested_at.is_(None))
            | (
                SalesContract.invoice_requested_at.is_not(None)
                & SalesContract.stock_item_id.is_distinct_from(stock_item_id)
            ),
        )
        .exists()
    )


_Counterpart = Callable[[InstrumentedAttribute[Any], InstrumentedAttribute[Any]], Select[Any]]


def unprojected_contract_check(*, label: str, grace: dt.timedelta, projection: _Counterpart) -> StateCheck:
    """A contract created more than `grace` ago for which
    `projection(contract_id, tenant_id)` — the caller's select over its own
    table — finds no row. The finding names the contract."""

    return StateCheck(
        label=label,
        source_model=SalesContract,
        source_row_id_column=SalesContract.id,
        where=lambda: (SalesContract.created_at < utcnow() - grace)
        & ~projection(SalesContract.id, SalesContract.tenant_id).correlate(SalesContract).exists(),
    )

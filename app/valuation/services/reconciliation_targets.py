"""What another context's nightly reconciliation checks against valuation's
table (rule 10; ADR-107), built here so the mapped `Valuation` class never leaves
this context (KAN-123 step 2, ADR-162).

Sales and Stock hold read-only references to a valuation (Sales'
`trade_in_valuation_id`, Stock's `valuation_ref_*`). Their `reconciliation.py`
asks four questions of valuation's rows, and each is a builder here that reads
nothing itself — the reconciliation run executes it:

- `valuation_reference_check` — the id names a valuation (an orphan otherwise).
- `valuation_exists` — an EXISTS for the id, optionally within one Betrieb, so
  Sales can report a deal naming another dealership's valuation (KAN-259).
- `valuation_not_used` — the Betrieb's valuation is not stamped used (a signed
  contract's trade-in valuation, KAN-115).
- `valuation_final_offer_differs` — the copied amount differs from the
  valuation's figure (Stock's `valuation_ref_amount`, KAN-115).

Valuations are tenant-private (ADR-029), and an EXISTS without a Betrieb could
be embedded in a live query to learn that another Betrieb's valuation exists,
out of `test_no_cross_context_orm_query.py`'s sight. So these builders are
confined to the reconciliation modules, which only report
(`tests/architecture/test_valuation_reconciliation_targets_only_in_reconciliation.py`).
"""

from typing import Any

from sqlalchemy import ColumnElement, Exists, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.valuation.models.valuation import Valuation

_Column = ColumnElement[Any] | InstrumentedAttribute[Any]


def valuation_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
) -> ReferenceCheck:
    """The reference `source_fk_column` holds names a valuation (or is null when `nullable`)."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=Valuation,
        target_id_column=Valuation.id,
        nullable=nullable,
    )


def valuation_exists(valuation_id: _Column, *, tenant_id: _Column | None = None) -> Exists:
    """A valuation with this id exists — in `tenant_id`'s Betrieb when one is given."""

    query = select(Valuation.id).where(Valuation.id == valuation_id)
    if tenant_id is not None:
        query = query.where(Valuation.tenant_id == tenant_id)
    return query.exists()


def valuation_not_used(valuation_id: _Column, *, tenant_id: _Column) -> Exists:
    """The Betrieb's valuation with this id exists and is not stamped used."""

    return (
        select(Valuation.id)
        .where(Valuation.tenant_id == tenant_id, Valuation.id == valuation_id, Valuation.used_at.is_(None))
        .exists()
    )


def valuation_final_offer_differs(valuation_id: _Column, *, amount: _Column) -> Exists:
    """The valuation with this id exists and its figure is not `amount` (a null amount differs)."""

    return (
        select(Valuation.id)
        .where(Valuation.id == valuation_id, Valuation.final_offer.is_distinct_from(amount))
        .exists()
    )


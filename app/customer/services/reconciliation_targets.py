"""What another context's nightly reconciliation checks against customer's
`customer` table (rule 10; ADR-107), built here so the mapped `Customer` class
never leaves the customer context (KAN-123 step 5, ADR-162).

Sales (offers, contracts, the deal projection), Valuation and Stock hold
customer ids (the three-column pattern). Their `reconciliation.py` asks:

- `customer_reference_check` — the id names a customer (an orphan otherwise).
- `customer_exists` — an EXISTS: the id names a customer, for a StateCheck
  that combines it with its own conditions.

Builders only: each reads nothing itself — the reconciliation run executes
it. An EXISTS over `customer` embedded in a live query would read across
out of `test_no_cross_context_orm_query.py`'s sight, so both are confined to
the reconciliation modules with the other customer predicates
(`tests/architecture/test_customer_predicates_only_in_reconciliation.py`).
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.customer.models.customer import Customer


def customer_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `customer` row."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=nullable,
        source_where=source_where,
    )


def customer_exists(customer_id: ColumnElement[Any] | InstrumentedAttribute[Any]) -> Exists:
    """An EXISTS: `customer_id` names a customer row."""

    return select(Customer.id).where(Customer.id == customer_id).exists()

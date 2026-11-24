"""What another context's nightly reconciliation checks against contact's
`contact` table (rule 10; ADR-107), built here so the mapped `Contact` class
never leaves the contact context (KAN-123 step 5, ADR-162).

Sales (offers, contracts, the deal projection), Valuation and Stock hold
contact ids (the three-column pattern). Their `reconciliation.py` asks:

- `contact_reference_check` — the id names a contact (an orphan otherwise).
- `contact_exists` — an EXISTS: the id names a contact, for a StateCheck
  that combines it with its own conditions.

Builders only: each reads nothing itself — the reconciliation run executes
it. An EXISTS over `contact` embedded in a live query would read across
out of `test_no_cross_context_orm_query.py`'s sight, so both are confined to
the reconciliation modules with the other contact predicates
(`tests/architecture/test_contact_predicates_only_in_reconciliation.py`).
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.contact.models.contact import Contact


def contact_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `contact` row."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=Contact,
        target_id_column=Contact.id,
        nullable=nullable,
        source_where=source_where,
    )


def contact_exists(contact_id: ColumnElement[Any] | InstrumentedAttribute[Any]) -> Exists:
    """An EXISTS: `contact_id` names a contact row."""

    return select(Contact.id).where(Contact.id == contact_id).exists()

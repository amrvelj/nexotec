"""What another context's nightly reconciliation checks against platform's
`dealer_group`, `dealership` and `location` tables (rule 10; ADR-107), built
here so the mapped `DealerGroup`, `Dealership` and `Location` classes never
leave platform (KAN-123 step 7, ADR-162). The `user` table's are in
`app/platform/identity/services/reconciliation_targets.py` (organisation
never imports identity).

Every context's tables hold a Betrieb (`tenant_id`), many a dealer group or
a location (the three-column pattern). Their `reconciliation.py` asks:

- `dealership_reference_check` / `dealer_group_reference_check` /
  `location_reference_check` / `labour_rate_category_reference_check` — the
  id names a row (an orphan otherwise). The last is Aftersales' (KAN-810): a
  labour position names the labour category it was priced in.
- `dealership_exists` — an EXISTS: the id names a Betrieb.
- `dealership_group_of` — a scalar subquery: the dealer group of the Betrieb
  the id names.
- `dealership_outside_group` — an EXISTS: the id names a Betrieb whose dealer
  group `IS DISTINCT FROM` the given one.
- `find_dealer_group_ids` / `find_dealership_group_ids` — plain reads for
  Contacts' vehicle-party stamp classification, which looks a batch of ids
  up in Python rather than in a check: which of them name a dealer group,
  and the dealer group of each that names a Betrieb. They run the SELECT the
  caller ran before and hand back values, not a statement.

The builders read nothing themselves — the reconciliation run executes them.
Each EXISTS correlates to `correlate` when it is given, as the checks it
replaced did, so the SQL is the one they ran. All of them answer about any
Betrieb, whoever asks, out of `test_no_cross_context_orm_query.py`'s and the
tenant filter's sight, so they are confined to the reconciliation modules
(`tests/architecture/test_platform_predicates_only_in_reconciliation.py`).
"""

import uuid
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import ColumnElement, Exists, ScalarSelect, Select, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute, Session

from app.core.reconciliation import ReferenceCheck
from app.platform.organisation.models.dealership import DealerGroup, Dealership, Location
from app.platform.organisation.models.labour_rate import LabourRateCategory

_Column = ColumnElement[Any] | InstrumentedAttribute[Any]


def _correlated(query: Select[Any], correlate: type[DeclarativeBase] | None) -> Select[Any]:
    return query.correlate(correlate) if correlate is not None else query


def _reference_check(
    target: type[DealerGroup] | type[Dealership] | type[Location] | type[LabourRateCategory],
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool,
    source_where: Callable[[], ColumnElement[bool]] | None,
) -> ReferenceCheck:
    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=target,
        target_id_column=target.id,
        nullable=nullable,
        source_where=source_where,
    )


def dealership_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `dealership` row."""

    return _reference_check(
        Dealership,
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        nullable=nullable,
        source_where=source_where,
    )


def dealer_group_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `dealer_group` row."""

    return _reference_check(
        DealerGroup,
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        nullable=nullable,
        source_where=source_where,
    )


def location_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `location` row."""

    return _reference_check(
        Location,
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        nullable=nullable,
        source_where=source_where,
    )


def dealership_exists(dealership_id: _Column, *, correlate: type[DeclarativeBase] | None = None) -> Exists:
    """An EXISTS: `dealership_id` names a Betrieb."""

    return _correlated(select(Dealership.id).where(Dealership.id == dealership_id), correlate).exists()


def dealership_group_of(dealership_id: _Column, *, correlate: type[DeclarativeBase] | None = None) -> ScalarSelect[Any]:
    """A scalar subquery: the dealer group of the Betrieb `dealership_id` names
    (NULL when it names none)."""

    return _correlated(
        select(Dealership.dealer_group_id).where(Dealership.id == dealership_id), correlate
    ).scalar_subquery()


def dealership_outside_group(
    dealership_id: _Column, group_id: _Column, *, correlate: type[DeclarativeBase] | None = None
) -> Exists:
    """An EXISTS: `dealership_id` names a Betrieb whose dealer group `IS
    DISTINCT FROM` `group_id`. A Betrieb that does not exist is not one — that
    is the id's ReferenceCheck finding."""

    return _correlated(
        select(Dealership.id)
        .where(Dealership.id == dealership_id)
        .where(Dealership.dealer_group_id.is_distinct_from(group_id)),
        correlate,
    ).exists()


def find_dealer_group_ids(db: Session, ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """The ids, of `ids`, that name a dealer group. One SELECT; the caller batches."""

    return list(db.scalars(select(DealerGroup.id).where(DealerGroup.id.in_(list(ids)))).all())


def find_dealership_group_ids(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """Betrieb id -> its dealer group's id, for each of `ids` that names a
    Betrieb. One SELECT; the caller batches."""

    return {
        dealership_id: group_id
        for dealership_id, group_id in db.execute(
            select(Dealership.id, Dealership.dealer_group_id).where(Dealership.id.in_(list(ids)))
        )
    }


def labour_rate_category_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `labour_rate_category` row (KAN-810: a labour position's category)."""

    return _reference_check(
        LabourRateCategory,
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        nullable=nullable,
        source_where=source_where,
    )

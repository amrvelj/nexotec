"""What another context's nightly reconciliation checks against platform's
`user` table (rule 10; ADR-107), built here so the mapped `User` class never
leaves platform (KAN-123 step 7, ADR-162). The actor columns' checks are
`actor_reference.py`'s; the organisation tables' builders are in
`app/platform/organisation/services/reconciliation_targets.py`.

Customers (a customer's advisor) and Sales (an offer's, contract's and
deal's responsible seller) hold user ids (the three-column pattern). Their
`reconciliation.py` asks:

- `user_reference_check` — the id names a user (an orphan otherwise).
- `user_is_deactivated` — an EXISTS: the id names a deactivated user.

Builders only: each reads nothing itself — the reconciliation run executes
it. The EXISTS correlates to `correlate` when it is given, as the checks it
replaced did, so the SQL is the one they ran. Both answer about any user,
whoever asks, so they are confined to the reconciliation modules
(`tests/architecture/test_platform_predicates_only_in_reconciliation.py`).
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.platform.identity.models.user import User


def user_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `user` row (the unaliased table, unlike
    `actor_reference_checks`, whose alias serves platform's own self-join)."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=User,
        target_id_column=User.id,
        nullable=nullable,
        source_where=source_where,
    )


def user_is_deactivated(
    user_id: ColumnElement[Any] | InstrumentedAttribute[Any], *, correlate: type[DeclarativeBase] | None = None
) -> Exists:
    """An EXISTS: `user_id` names a user whose status is `deactivated`."""

    query: Select[Any] = select(User.id).where(User.id == user_id, User.status == "deactivated")
    return (query.correlate(correlate) if correlate is not None else query).exists()

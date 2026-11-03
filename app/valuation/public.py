"""The only surface other contexts may import from valuation. Import-
linter's contract allows `app.<other-context>` to import
`app.valuation.public`, never `app.valuation.models` /
`app.valuation.services` / `app.valuation.api` directly.

`get_valuation_or_404`/`list_valid_valuations_for_vehicle` are read
surfaces for Sales's trade-in container (PR-5: "an existing valid
valuation is offered before a new one is made") and for inventory, which
copies the pointer onto a trade-in's pipeline stock item (KAN-101).

`consume_valuation_for_contract` is the one cross-context WRITE, with
`revert_valuation_use` as its compensating action — Pattern B (ADR-047),
each with its own commit — both called by Sales's `confirm_contract`
(KAN-101), never by anyone reaching into app.valuation.services directly.
"""

import uuid

from sqlalchemy.orm import Session

from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.services.valuation import consume_for_contract as _consume_for_contract
from app.valuation.services.valuation import (
    derive_status,
    get_valuation_or_404,
    list_valid_valuations_for_vehicle,
)
from app.valuation.services.valuation import revert_use as _revert_use


def consume_valuation_for_contract(
    db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID, actor_id: uuid.UUID | None
) -> bool:
    """Marks the valuation used unless it already is; True when this call
    set it. Raises ConflictError (`details.reason == "valuation_expired"`)
    for a valuation past its validity. Own commit."""

    valuation = get_valuation_or_404(db, tenant_id, valuation_id)
    return _consume_for_contract(db, valuation=valuation, actor_id=actor_id)


def revert_valuation_use(
    db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID, actor_id: uuid.UUID | None
) -> Valuation:
    valuation = get_valuation_or_404(db, tenant_id, valuation_id)
    return _revert_use(db, valuation=valuation, actor_id=actor_id)


__all__ = [
    "Valuation",
    "ValuationSource",
    "consume_valuation_for_contract",
    "derive_status",
    "get_valuation_or_404",
    "list_valid_valuations_for_vehicle",
    "revert_valuation_use",
]

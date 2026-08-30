"""The only surface other contexts may import from valuation. Import-
linter's contract allows `app.<other-context>` to import
`app.valuation.public`, never `app.valuation.models` /
`app.valuation.services` / `app.valuation.api` directly.

`get_valuation_or_404`/`list_valid_valuations_for_vehicle` are read
surfaces for Sales's trade-in container (PR-5: "an existing valid
valuation is offered before a new one is made"). `mark_valuation_used` is
the one cross-context WRITE — Pattern B (ADR-047), own commit — called by
Sales at contract confirmation (PR-6), never by anyone reaching into
app.valuation.services directly.
"""

import uuid

from sqlalchemy.orm import Session

from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.services.valuation import (
    derive_status,
    get_valuation_or_404,
    list_valid_valuations_for_vehicle,
)
from app.valuation.services.valuation import mark_used as _mark_used


def mark_valuation_used(db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID, actor_id: uuid.UUID | None) -> Valuation:
    valuation = get_valuation_or_404(db, tenant_id, valuation_id)
    return _mark_used(db, valuation=valuation, actor_id=actor_id)


__all__ = [
    "Valuation",
    "ValuationSource",
    "derive_status",
    "get_valuation_or_404",
    "list_valid_valuations_for_vehicle",
    "mark_valuation_used",
]

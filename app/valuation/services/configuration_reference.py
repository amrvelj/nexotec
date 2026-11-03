"""KAN-270 — vehicle's daily purge of configurations no host ever took asks
Valuation which of them a valuation names now (the safety net beside
`valuation.created`, which carries `configurationId`;
`app/vehicle/services/configuration_retention.py`). Every valuation counts —
draft, expired or used, in every Betrieb: valuations are never deleted
(ADR-066), so a configuration one names is kept with it. A SELECT only."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.valuation.models.valuation import Valuation


def referenced_configuration_ids(db: Session, configuration_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    ids = list(dict.fromkeys(configuration_ids))
    if not ids:
        return set()
    return {
        value
        for value in db.scalars(select(Valuation.configuration_id).where(Valuation.configuration_id.in_(ids)))
        if value is not None
    }

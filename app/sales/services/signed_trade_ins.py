"""Which trade-in valuations a signed contract carries (KAN-115) — the one
fact the valuation context needs from Sales: only a signed deal stamps a
valuation «Verwendet» (Anto, 2026-10-07), so the hand stamp is allowed, and
offered on screen, only for a valuation a signed contract carries.

Read only. Imports no other context, so app.valuation reaches it through
app.sales.public without an import cycle (app.sales.services.contract
imports app.valuation.public).
"""

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import SalesContract


def valuations_carried_by_signed_contracts(
    db: Session, *, tenant_id: uuid.UUID, valuation_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The subset of `valuation_ids` that at least one signed contract of
    this tenant carries as its trade-in valuation. Signed means `signed_at`
    is set, whatever the status since: a cancelled contract stays signed on
    its valuation (ADR-066)."""

    if not valuation_ids:
        return set()
    carried = db.scalars(
        select(SalesContract.trade_in_valuation_id)
        .where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.trade_in_valuation_id.in_(list(valuation_ids)),
            SalesContract.signed_at.is_not(None),
        )
        .distinct()
    ).all()
    return {valuation_id for valuation_id in carried if valuation_id is not None}

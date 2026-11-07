"""KAN-270 — what vehicle needs to know about the configurations Sales'
offers and contracts store, so it can delete one no host ever took 30 days
after it was created and keep for good one a host took (Anto, 2026-11-03,
option C; `app/vehicle/services/configuration_retention.py`).

- `publish_configuration_attached` — the past-tense fact, in the
  transaction that stores the id: `sales.offer.configuration_attached` /
  `sales.contract.configuration_attached`, published whenever an offer or a
  contract starts holding a configuration id it did not hold — its car
  (Path B, FR-C-12; a copied offer's own copy, KAN-188; a contract taking its
  offer's) or its trade-in (the valuation's configuration, KAN-10). Payload
  `configurationId`, `slot` (`vehicle` | `trade_in`) and the deal's number.
  Detaching publishes nothing: a configuration once taken is kept.
- `referenced_configuration_ids` — the read-only safety net vehicle's daily job
  asks: which of these ids any offer or contract names now, in either slot,
  whatever its status or Betrieb (a reference is never deleted under its
  host, even a mismatched one reconciliation reports).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.sales.models.contract import SalesContract
from app.sales.models.offer import SalesOffer

SLOT_VEHICLE = "vehicle"
SLOT_TRADE_IN = "trade_in"

_EVENT_PRODUCER = "sales"
_HELD_COLUMNS = (
    SalesOffer.configuration_id,
    SalesOffer.trade_in_configuration_id,
    SalesContract.configuration_id,
    SalesContract.trade_in_configuration_id,
)


def publish_configuration_attached(
    db: Session, *, deal: SalesOffer | SalesContract, configuration_id: uuid.UUID, slot: str
) -> None:
    """Writes the outbox row in the caller's transaction (rule 4); the
    caller has flushed the deal and owns the commit."""

    if isinstance(deal, SalesContract):
        event_type, aggregate_type, number = (
            "sales.contract.configuration_attached",
            "sales_contract",
            {"contractNumber": deal.contract_number},
        )
    else:
        event_type, aggregate_type, number = (
            "sales.offer.configuration_attached",
            "sales_offer",
            {"offerNumber": deal.offer_number},
        )
    publish(
        db,
        OutboxEvent(
            event_type=event_type,
            tenant_id=deal.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type=aggregate_type,
            aggregate_id=deal.id,
            payload={**number, "configurationId": str(configuration_id), "slot": slot},
        ),
    )


def publish_held_configurations(db: Session, *, deal: SalesOffer | SalesContract) -> None:
    """For a deal written with its configuration ids already set (a copied
    offer, a contract from an offer): one fact per slot it holds."""

    if deal.configuration_id is not None:
        publish_configuration_attached(db, deal=deal, configuration_id=deal.configuration_id, slot=SLOT_VEHICLE)
    if deal.trade_in_configuration_id is not None:
        publish_configuration_attached(
            db, deal=deal, configuration_id=deal.trade_in_configuration_id, slot=SLOT_TRADE_IN
        )


def referenced_configuration_ids(db: Session, configuration_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """A SELECT per slot column; the caller batches the ids."""

    ids = list(dict.fromkeys(configuration_ids))
    if not ids:
        return set()
    held: set[uuid.UUID] = set()
    for column in _HELD_COLUMNS:
        held.update(value for value in db.scalars(select(column).where(column.in_(ids))) if value is not None)
    return held

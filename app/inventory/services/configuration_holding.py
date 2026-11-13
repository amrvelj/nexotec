"""KAN-513 — one configuration, one stock item, for the writers that key on
a contract: the FR-I-02/02b consumers of `sales.contract.confirmed`.

A configuration is one car's technical data (FR-C-11, KAN-509), and the
partial unique index `uq_stock_item_tenant_id_configuration_id` refuses a
second stock item of the Betrieb that holds it. A contract carries its
offer's configuration, so a contract cancelled and a second one signed on
the same offer would carry it into a second pipeline item. Anto's ruling
(2026-11-03, option A):

- the second order **reuses the first pipeline car while it is still on
  stock and free** (it has not left stock and no contract reserves it): the
  car is reserved for the new contract and Stock publishes
  `inventory.stock_item.reassigned`, so Sales records the car on that
  contract as it does on `inventory.stock_item.added`;
- once that car **has gone elsewhere** (another contract reserves it, or it
  has left stock), the new order gets **its own copy of the configuration**
  (`vehicle.public.copy_configuration_for_host`, FR-C-11) and a new pipeline
  item, as before.

A trade-in's pipeline item is never reused for another contract — it is the
seller's car of that contract (KAN-632) — so a configuration a trade-in item
already holds is copied for the next one (technical; one valuation may back
several contracts, Anto 2026-09-29).

The copy commits on a session of its own (ADR-047); a consumer that fails
after it leaves the copy untaken, and Vehicle's daily purge deletes an untaken
configuration after 30 days (KAN-270) — the compensation, with no extra state.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import StockItem
from app.inventory.services.stock_item import _EVENT_PRODUCER, _lock_configuration
from app.vehicle.public import copy_configuration_for_host

CONFIGURATION_INDEX = "uq_stock_item_tenant_id_configuration_id"
REASSIGNED_EVENT = "inventory.stock_item.reassigned"


def is_configuration_conflict(exc: IntegrityError) -> bool:
    """The partial unique index refused the write (rather than another
    constraint)."""

    return CONFIGURATION_INDEX in str(exc.orig)


def configuration_held_error(held_by: StockItem) -> ConflictError:
    return ConflictError(
        f"This configuration already belongs to stock item {held_by.stock_number}.",
        details={
            "reason": "configuration_held_by_stock_item",
            "stockItemId": str(held_by.id),
            "stockNumber": held_by.stock_number,
        },
    )


def configuration_holder(db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID) -> StockItem | None:
    """The stock item of the Betrieb holding the configuration, row-locked.
    The caller holds `_lock_configuration`."""

    return db.scalars(
        select(StockItem)
        .where(StockItem.tenant_id == tenant_id, StockItem.configuration_id == configuration_id)
        .order_by(StockItem.created_at)
        .limit(1)
        .with_for_update()
    ).first()


@dataclasses.dataclass(frozen=True)
class Settled:
    """What the order's configuration resolves to: the car to reuse (none
    for a new item) and the configuration a new item takes (the order's, or
    its copy)."""

    reuse: StockItem | None
    configuration_id: uuid.UUID


def _is_free_on_stock(item: StockItem) -> bool:
    return item.left_stock_at is None and item.active_reservation_id is None


def settle_ordered_configuration(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID, contract_id: uuid.UUID, may_reuse: bool
) -> Settled:
    """Under the per-(tenant, configuration) lock the create and the attach
    take (KAN-509): no holder → a new item with this configuration; a holder
    this contract already reserves (a redelivery) or, when `may_reuse`, one
    still free on stock → reuse it; any other holder → a new item with a
    copy of the configuration."""

    _lock_configuration(db, tenant_id=tenant_id, configuration_id=configuration_id)
    holder = configuration_holder(db, tenant_id=tenant_id, configuration_id=configuration_id)
    if holder is None:
        return Settled(reuse=None, configuration_id=configuration_id)
    if may_reuse and (holder.reserved_by_contract_id == contract_id or _is_free_on_stock(holder)):
        return Settled(reuse=holder, configuration_id=configuration_id)
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        copy = copy_configuration_for_host(own, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=None)
    return Settled(reuse=None, configuration_id=copy.id)


def publish_reassigned(db: Session, *, item: StockItem, contract_id: uuid.UUID) -> None:
    """The fact Sales links the reused car to the new contract by — the
    payload `inventory.stock_item.added` carries for a contract's car."""

    publish(
        db,
        OutboxEvent(
            event_type=REASSIGNED_EVENT,
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={
                "stockNumber": item.stock_number,
                "vehicleLabel": item.vehicle_label,
                "configurationId": str(item.configuration_id),
                "originContractId": str(contract_id),
                "originRole": "manual_configuration",
            },
        ),
    )

"""KAN-513 — one configuration, one stock item, for the writers that key on
a contract: the FR-I-02/02b consumers of `sales.contract.confirmed`.

A configuration is one car's technical data (FR-C-11, KAN-509), and the
partial unique index `uq_stock_item_tenant_id_configuration_id` refuses a
second stock item of the Betrieb that holds it. A contract carries its
offer's configuration, so a contract cancelled and a second one signed on
the same offer would carry it into a second pipeline item. Anto's ruling
(2026-11-03, option A):

- the second order **reuses the first ordered car while it is still on
  stock and free** (a manual configuration's pipeline item — `pipeline_ref`
  `contract:<id>:manual` — that has not left stock and that no contract
  reserves): the car is reserved for the new contract and Stock publishes
  `inventory.stock_item.reassigned` (`contractId`), so Sales records the car
  on that contract;
- once that car **has gone elsewhere** (another contract reserves it, it has
  left stock, or the configuration is held by a car that is not an ordered
  one), the new order gets **its own copy of the configuration**
  (`vehicle.public.copy_configuration_for_host`, FR-C-11) and a new pipeline
  item, as before.

A trade-in's pipeline item is never reused — it is the seller's car of its
own contract (KAN-632) — so a configuration a trade-in item holds is copied
for the next one (technical; one valuation may back several contracts,
Anto 2026-09-29).

**Compensation (ADR-047).** The copy commits on a session of its own. If the
rest of the consumer's handler fails, `discard_copies` undoes it
(`vehicle.public.discard_configuration_copy`), as `copy_offer` does. A
failure after the handler — the consumer harness's own commit — cannot be
caught here; the copy is then untaken, and Vehicle's daily purge deletes an
untaken configuration after 30 days (KAN-270). The retry makes a new copy.

**Residual (disclosed).** When the first contract's cancellation has not
reached Stock yet (its delivery failed and waits for a retry), its car still
reads as reserved, so the second contract gets a copy and a second ordered
car instead of the reuse. The nightly orphan sweep then frees the first car.
Nothing is lost; the dealer sees two pipeline cars for one order until one
is removed.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.outbox import OutboxEvent, publish
from app.inventory.models.stock_item import StockItem
from app.inventory.services.stock_item import _EVENT_PRODUCER, _lock_configuration
from app.vehicle.public import copy_configuration_for_host, discard_configuration_copy

REASSIGNED_EVENT = "inventory.stock_item.reassigned"
_ORDERED_CAR_REF = ":manual"


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
    for a new item), the configuration a new item takes (the order's, or its
    copy), and whether that is a copy made here."""

    reuse: StockItem | None
    configuration_id: uuid.UUID
    copied: bool = False


def _is_free_ordered_car(item: StockItem) -> bool:
    return (
        (item.pipeline_ref or "").endswith(_ORDERED_CAR_REF)
        and item.left_stock_at is None
        and item.active_reservation_id is None
    )


def settle_ordered_configuration(
    db: Session, *, tenant_id: uuid.UUID, configuration_id: uuid.UUID, contract_id: uuid.UUID, may_reuse: bool
) -> Settled:
    """Under the per-(tenant, configuration) lock the create and the attach
    take (KAN-509): no holder → a new item with this configuration; a holder
    this contract already reserves (a redelivery) or, when `may_reuse`, a
    free ordered car → reuse it; any other holder → a new item with a copy
    of the configuration."""

    _lock_configuration(db, tenant_id=tenant_id, configuration_id=configuration_id)
    holder = configuration_holder(db, tenant_id=tenant_id, configuration_id=configuration_id)
    if holder is None:
        return Settled(reuse=None, configuration_id=configuration_id)
    if may_reuse and (holder.reserved_by_contract_id == contract_id or _is_free_ordered_car(holder)):
        return Settled(reuse=holder, configuration_id=configuration_id)
    with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
        copy = copy_configuration_for_host(own, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=None)
    return Settled(reuse=None, configuration_id=copy.id, copied=True)


def discard_copies(db: Session, *, tenant_id: uuid.UUID, configuration_ids: list[uuid.UUID]) -> None:
    """The compensating action for copies a failed handler made, each on a
    session of its own (ADR-047). A discard that fails itself is left to the
    30-day purge of untaken configurations."""

    for configuration_id in configuration_ids:
        with Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False) as own:
            try:
                discard_configuration_copy(own, tenant_id=tenant_id, configuration_id=configuration_id, actor_id=None)
            except Exception:  # noqa: BLE001 — the handler's own failure is the one re-raised
                own.rollback()


def publish_reassigned(db: Session, *, item: StockItem, contract_id: uuid.UUID) -> None:
    """The fact Sales links the reused car to the new contract by.
    `originRole` as on the entity's other facts (the car was ordered for a
    manual configuration); its own key, `contractId`: the item's
    `originContractId` on its other facts
    (`purchased`, `storno`, from `pipeline_ref`) still names the contract it
    was ordered for."""

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
                "contractId": str(contract_id),
                "originRole": "manual_configuration",
            },
        ),
    )

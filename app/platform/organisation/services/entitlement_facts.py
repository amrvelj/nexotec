"""A Betrieb's entitlement facts for other contexts (KAN-808 PR-1d; ADR-222):
what Platform states, through the outbox, when a module becomes active for a
Betrieb — so a context above platform that keeps something per module (the
planner's starting sets, for `aftersales`) learns it from Platform's own fact,
never by polling and never inside Platform's transaction (rules 4–6). Platform
cannot call such a context: calls go down the layers only.

The fact:

- `platform.module_entitlement.granted` — a module became active for the
  Betrieb: a first grant, or a re-grant of one withdrawn earlier. Published by
  `set_entitlements` (`entitlement.py`) once per module that becomes active, in
  the entitlement change's own transaction — the platform_admin's PUT and
  onboarding's `commit=False` path alike. A module that stays active, or one
  withdrawn, publishes nothing.

Payload: `dealershipId`, `module` (a `ModuleCode` value), `validFrom` (when
the module became active, ISO 8601 UTC) and `entitlementsVersion` (the
Betrieb's `entitlements_version` after the change). The aggregate is the
`module_entitlement` row. A consumer that needs the current set reads it
through `platform.public` (delivery is unordered and at-least-once).

`restate_module_granted` states the fact again for every Betrieb that holds a
module now — for Betriebe that held it before the fact existed
(`scripts/restate_module_entitlements.py`, dry run by default). A restatement
is the same fact with the same payload shape: its consumers must already be
harmless on a repeat (rule 6, and ADR-222's marker for the starting sets).

`publish` adds and flushes in the caller's session; the caller's commit makes
the entitlement row and its fact land together (rule 4).
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox import OutboxEvent, publish
from app.core.permissions import ModuleCode
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services.dealer_dealerships import dealer_dealerships_only

MODULE_ENTITLEMENT_GRANTED_EVENT = "platform.module_entitlement.granted"
_PRODUCER = "platform"
_AGGREGATE = "module_entitlement"


def granted_payload(*, dealership: Dealership, row: ModuleEntitlement) -> dict[str, Any]:
    return {
        "dealershipId": str(dealership.id),
        "module": row.module.value,
        "validFrom": row.valid_from.isoformat(),
        "entitlementsVersion": dealership.entitlements_version,
    }


def publish_module_granted(db: Session, *, dealership: Dealership, row: ModuleEntitlement) -> None:
    """Adds `platform.module_entitlement.granted` for `row`, which has just
    become active. The caller has flushed the row and written the Betrieb's
    new `entitlements_version`."""

    if row.id is None:
        raise ValueError("A module entitlement fact needs the flushed row's id.")
    publish(
        db,
        OutboxEvent(
            event_type=MODULE_ENTITLEMENT_GRANTED_EVENT,
            tenant_id=dealership.id,
            producer=_PRODUCER,
            aggregate_type=_AGGREGATE,
            aggregate_id=row.id,
            payload=granted_payload(dealership=dealership, row=row),
        ),
    )


@dataclass(frozen=True)
class RestatedGrant:
    dealership_id: uuid.UUID
    legal_name: str
    entitlement_id: uuid.UUID


def restate_module_granted(db: Session, *, module: ModuleCode) -> list[RestatedGrant]:
    """States `platform.module_entitlement.granted` again for every dealer
    Betrieb that holds `module` now, an offboarded one aside (it uses nothing
    any more). Adds and flushes; the caller commits, or rolls back for a dry
    run. Returns what was stated, by Betrieb id."""

    now = utcnow()
    rows = db.execute(
        select(ModuleEntitlement, Dealership)
        .join(Dealership, Dealership.id == ModuleEntitlement.dealership_id)
        .where(
            ModuleEntitlement.module == module,
            dealer_dealerships_only(),
            Dealership.status != DealershipStatus.OFFBOARDED,
        )
        .order_by(Dealership.id)
    ).all()
    restated: list[RestatedGrant] = []
    for row, dealership in rows:
        if not row.is_active(now):
            continue
        publish_module_granted(db, dealership=dealership, row=row)
        restated.append(
            RestatedGrant(dealership_id=dealership.id, legal_name=dealership.legal_name, entitlement_id=row.id)
        )
    return restated

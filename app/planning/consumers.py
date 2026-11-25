"""The planning context's outbox consumers, registered in
`app/worker.py::register_handlers`:

- `platform.module_entitlement.granted` ->
  `planning.starting_sets_on_module_granted`
  (`handle_module_entitlement_granted_message`, KAN-808 PR-1d; ADR-222): when a
  Betrieb holds Aftersales, Nexotec's technician and workplace types are
  copied into its planner lists, once ever (`services/starting_sets.py`). A
  grant of any other module is a no-op. Idempotent twice over: by `eventId`
  (the harness, `consume_once`, which commits) and by the Betrieb's marker row,
  so a re-grant or a restated fact with a new `eventId` copies nothing either.

The fact is acted on as stated, without re-reading the entitlement: a Betrieb
that held Aftersales for a moment got its starting sets, which stay inert
without the module (delivery is unordered; the sets are copied once whatever
arrives later).
"""

from sqlalchemy.orm import Session

from app.core.outbox_model import OutboxMessage
from app.core.permissions import ModuleCode
from app.planning.services.starting_sets import copy_starting_sets
from app.platform.public import MODULE_ENTITLEMENT_GRANTED_EVENT

STARTING_SETS_CONSUMER_NAME = "planning.starting_sets_on_module_granted"
STARTING_SETS_EVENT_TYPE = MODULE_ENTITLEMENT_GRANTED_EVENT


def handle_module_entitlement_granted_message(db: Session, message: OutboxMessage) -> None:
    if message.payload.get("module") != ModuleCode.AFTERSALES.value:
        return
    if message.tenant_id is None:
        raise ValueError(f"{message.event_type} message {message.id} has no tenant_id.")
    copy_starting_sets(db, dealership_id=message.tenant_id, source_event_id=message.id)

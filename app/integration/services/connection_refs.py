"""What another context sees of a connection and its licence rows (KAN-123).

`app.integration.public` hands out these plain values, never the mapped
`IntegrationConnection` / `IntegrationEntitlement` rows: with a row in hand,
another context could select, join, change and flush integration's tables
while the import-linter passes, since the import went through `public`. The
contract a remote service would offer (ADR-015) is a value and the calls that
take it back, not a table mapping.

- `get_enabled_connection` answers a `ConnectionRef`: the connection's id, the
  one thing a caller outside this context reads, and the Betrieb it belongs
  to. `call_capability` and `resolve_adapter` (`services/gateway.py`) take it
  back and work on the row themselves, after checking that the row is that
  Betrieb's: a reference built from a stored id for the caller's own Betrieb
  cannot reach another Betrieb's provider account.
- `get_entitlement` answers an `EntitlementState`: whether the licence option
  is granted and when that was last established — still `None` for "never
  probed or declared", a fact the registry reports and the caller interprets
  (`app.vehicle.services.catalogue_entitlements`).
- The licence reads (`get_entitlement`, `vin_decode_granted`,
  `valuation_granted`) take the `ConnectionRef` back too (KAN-711) and read
  only a row of the Betrieb it names: for another Betrieb's connection, or a
  platform-wide one, they answer exactly as for an unknown id (`None`, not
  granted), so a reference rebuilt from a stored id learns nothing about
  another Betrieb's contract, not even that the connection exists (rule 7).

Integration's own code keeps using `services/connections.py`'s functions of
the same names, which return the rows.
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.integration.models.connection import IntegrationConnection
from app.integration.services import connections as connection_service


@dataclasses.dataclass(frozen=True)
class ConnectionRef:
    """An enabled connection as another context holds it: its id and its Betrieb."""

    id: uuid.UUID
    tenant_id: uuid.UUID


@dataclasses.dataclass(frozen=True)
class EntitlementState:
    """One licence option of one connection, as last probed or declared."""

    granted: bool
    checked_at: dt.datetime


def get_enabled_connection(db: Session, *, tenant_id: uuid.UUID, provider_code: str) -> ConnectionRef | None:
    """The tenant's enabled connection to `provider_code`, or `None` (none, or disabled)."""

    row = connection_service.get_enabled_connection(db, tenant_id=tenant_id, provider_code=provider_code)
    # The lookup filtered on `tenant_id`, so the row is that Betrieb's (a platform-wide
    # connection, which has none, is never found here and never reachable by a reference).
    return None if row is None else ConnectionRef(id=row.id, tenant_id=tenant_id)


def _own_connection_id(db: Session, connection: ConnectionRef) -> uuid.UUID | None:
    """The id behind `connection` when its row is the Betrieb's the reference names,
    else `None` (deleted, another Betrieb's, or platform-wide, which has no Betrieb).
    `get` finds the row the caller's own lookup left in its session without a query."""

    row = db.get(IntegrationConnection, connection.id)
    if row is None or row.tenant_id is None or row.tenant_id != connection.tenant_id:
        return None
    return row.id


def get_entitlement(db: Session, *, connection: ConnectionRef, capability_code: str) -> EntitlementState | None:
    """The connection's row for one licence option, or `None` when it was never probed
    or declared — and, the same, when the reference does not name its Betrieb's row."""

    connection_id = _own_connection_id(db, connection)
    if connection_id is None:
        return None
    row = connection_service.get_entitlement(db, connection_id=connection_id, capability_code=capability_code)
    return None if row is None else EntitlementState(granted=row.granted, checked_at=row.checked_at)


def vin_decode_granted(db: Session, *, connection: ConnectionRef) -> bool:
    """KAN-549: may this connection send a VIN query — its `vin` row, read strictly
    (no row is not granted; nor is a reference that does not name its Betrieb's row)."""

    connection_id = _own_connection_id(db, connection)
    return connection_id is not None and connection_service.vin_decode_granted(db, connection_id=connection_id)


def valuation_granted(db: Session, *, connection: ConnectionRef) -> bool:
    """KAN-552: may this connection ask for a valuation — its `bewertung` row, read
    strictly, like `vin_decode_granted`."""

    connection_id = _own_connection_id(db, connection)
    return connection_id is not None and connection_service.valuation_granted(db, connection_id=connection_id)

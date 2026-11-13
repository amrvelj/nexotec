"""What another context sees of a connection and its licence rows (KAN-123).

`app.integration.public` hands out these plain values, never the mapped
`IntegrationConnection` / `IntegrationEntitlement` rows: with a row in hand,
another context could select, join, change and flush integration's tables
while the import-linter passes, since the import went through `public`. The
contract a remote service would offer (ADR-015) is a value and the calls that
take it back, not a table mapping.

- `get_enabled_connection` answers a `ConnectionRef`: the connection's id, the
  one thing a caller outside this context reads. `call_capability` and
  `resolve_adapter` (`services/gateway.py`) take it back and work on the row
  themselves.
- `get_entitlement` answers an `EntitlementState`: whether the licence option
  is granted and when that was last established — still `None` for "never
  probed or declared", a fact the registry reports and the caller interprets
  (`app.vehicle.services.catalogue_entitlements`).

Integration's own code keeps using `services/connections.py`'s functions of
the same names, which return the rows.
"""

import dataclasses
import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.integration.services import connections as connection_service


@dataclasses.dataclass(frozen=True)
class ConnectionRef:
    """An enabled connection as another context holds it: its id."""

    id: uuid.UUID


@dataclasses.dataclass(frozen=True)
class EntitlementState:
    """One licence option of one connection, as last probed or declared."""

    granted: bool
    checked_at: dt.datetime


def get_enabled_connection(db: Session, *, tenant_id: uuid.UUID, provider_code: str) -> ConnectionRef | None:
    """The tenant's enabled connection to `provider_code`, or `None` (none, or disabled)."""

    row = connection_service.get_enabled_connection(db, tenant_id=tenant_id, provider_code=provider_code)
    return None if row is None else ConnectionRef(id=row.id)


def get_entitlement(db: Session, *, connection_id: uuid.UUID, capability_code: str) -> EntitlementState | None:
    """The connection's row for one licence option, or `None` when it was never probed or declared."""

    row = connection_service.get_entitlement(db, connection_id=connection_id, capability_code=capability_code)
    return None if row is None else EntitlementState(granted=row.granted, checked_at=row.checked_at)

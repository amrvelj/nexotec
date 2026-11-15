"""Integration's contribution to the Betrieb's status page (KAN-223; D-A-38,
FR-A-43): one item per enabled connection of the Betrieb, derived from the
connection's own state on every read — the status page never computes a
source's health itself.

- `error` (the last «Test connection» failed) → blocked;
- `expired`, or `expires_at` reached → blocked (Anto, 2026-11-14: blocked once
  expired);
- `expires_at` within 30 days → warning (Anto, 2026-11-14: a warning from 30
  days before expiry). This context already tells the managers at T-30/14/7
  (`administration.integration_expiring`, KAN-222), so the item says
  `notified_by_source` and the status job does not notify again;
- `not_configured` (created or re-enabled, not tested since) → warning: the
  connection has not proved that its credentials work;
- `connected` → ok.

A connection switched off (`enabled` false, `disabled`) is the Betrieb's
deliberate choice and is not an item. Platform-scope connections have no
Betrieb and are not read. Registered with `app.core.status` by
`app/status_registry.py`; platform never imports this module.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.status import StatusItem, StatusState
from app.integration.models.connection import ConnectionScope, ConnectionStatus, IntegrationConnection
from app.integration.models.provider import IntegrationProvider

SOURCE = "integration"
EXPIRY_WARNING_DAYS = 30
_LINK = "/administration/integrations"


def _item(connection: IntegrationConnection, provider_name: str, now: dt.datetime) -> StatusItem:
    params: dict[str, str | int] = {"connection": connection.display_name, "provider": provider_name}
    notified_by_source = False
    expires_at = connection.expires_at
    if connection.status == ConnectionStatus.ERROR:
        state, message = StatusState.BLOCKED, "error"
    elif connection.status == ConnectionStatus.EXPIRED or (expires_at is not None and expires_at <= now):
        state, message = StatusState.BLOCKED, "expired"
    elif expires_at is not None and expires_at - now <= dt.timedelta(days=EXPIRY_WARNING_DAYS):
        state, message = StatusState.WARNING, "expiring"
        params["days"] = max(0, (expires_at - now).days)
        notified_by_source = True
    elif connection.status == ConnectionStatus.NOT_CONFIGURED:
        state, message = StatusState.WARNING, "notTested"
    else:
        state, message = StatusState.OK, "ok"
    return StatusItem(
        key=f"integration.connection.{connection.id}",
        source=SOURCE,
        state=state,
        message_key=f"statusPage.items.integration.{message}",
        link=_LINK,
        params=params,
        notified_by_source=notified_by_source,
    )


class IntegrationStatusProvider:
    source = SOURCE

    def items(self, db: Session, *, dealership_id: uuid.UUID, now: dt.datetime) -> list[StatusItem]:
        rows = db.execute(
            select(IntegrationConnection, IntegrationProvider.display_name)
            .join(IntegrationProvider, IntegrationProvider.id == IntegrationConnection.provider_id)
            .where(
                IntegrationConnection.scope == ConnectionScope.TENANT,
                IntegrationConnection.tenant_id == dealership_id,
                IntegrationConnection.enabled.is_(True),
                IntegrationConnection.status != ConnectionStatus.DISABLED,
            )
            .order_by(IntegrationProvider.display_name, IntegrationConnection.display_name, IntegrationConnection.id)
        ).all()
        return [_item(connection, provider_name, now) for connection, provider_name in rows]

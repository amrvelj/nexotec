"""Integrations' export declaration (KAN-218; FR-A-44): the Betrieb's
connections, their capability entitlements, the call log and the
notifications. Never a secret: a connection's credentials live in the
secrets manager, the row holds only a write-only reference (ADR-024), and the
stored provider payloads are the provider's data kept for support.

At the deletion after the retention period each connection goes through
`delete_connection`, which deletes its secrets in the secrets manager too —
deleting the rows alone would leave the credentials behind.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.export import ExportedTable, ExportScope, TableExporter, table_of
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.caption import IntegrationProviderCaption, IntegrationProviderCaptionFetch
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.notification import IntegrationNotification
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef
from app.integration.services.connections import delete_connection

ERASURE_ACTOR_REASON = "dealership_retention_expired"

_connection = table_of(IntegrationConnection)


def _by_connection(table):
    return lambda scope: table.c.connection_id.in_(
        select(_connection.c.id).where(_connection.c.tenant_id == scope.dealership_id)
    )


def _tenant(model, **kwargs) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id, **kwargs)


class IntegrationExporter(TableExporter):
    context = "integration"

    def exported_tables(self) -> Sequence[ExportedTable]:
        entitlement = table_of(IntegrationEntitlement)
        secret_ref = table_of(IntegrationSecretRef)
        return (
            _tenant(IntegrationConnection),
            ExportedTable(table=entitlement, scope=_by_connection(entitlement), marks_write=False),
            ExportedTable(
                table=secret_ref,
                scope=_by_connection(secret_ref),
                in_archive=False,
                exclusion_reason="A write-only reference into the secrets manager (ADR-024); never returned.",
            ),
            # The calls and notices happen without the Betrieb writing anything.
            _tenant(IntegrationCallLog, marks_write=False),
            _tenant(IntegrationNotification, marks_write=False),
            _tenant(
                IntegrationCallPayload,
                in_archive=False,
                exclusion_reason="The provider's stored request and answer, encrypted, kept for support only.",
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        catalogue = "Nexotec's provider catalogue, the same for every Betrieb."
        return {
            IntegrationProvider.__tablename__: catalogue,
            IntegrationProviderCaption.__tablename__: catalogue,
            IntegrationProviderCaptionFetch.__tablename__: catalogue,
        }

    def before_erase(self, db: Session, scope: ExportScope) -> int:
        """Each connection with its secrets (secrets manager included) and
        entitlements, through the one deletion integration has; each commits."""

        connections = db.scalars(
            select(IntegrationConnection).where(IntegrationConnection.tenant_id == scope.dealership_id)
        ).all()
        for connection in connections:
            delete_connection(db, connection=connection, confirm=True, actor_id=None)
        return len(connections)

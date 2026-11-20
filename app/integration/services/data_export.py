"""Integrations' export declaration (KAN-218; FR-A-44): the Betrieb's
connections, their capability entitlements, the call log and the
notifications, and its adapted AI prompts. Never a secret: a connection's credentials live in the
secrets manager, the row holds only a write-only reference (ADR-024), and the
stored provider payloads are the provider's data kept for support.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import select

from app.core.export import ExportedTable, TableExporter, table_of
from app.integration.models.ai_prompt import IntegrationAiPrompt
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.caption import IntegrationProviderCaption, IntegrationProviderCaptionFetch
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.mail import MailLinkState, MessageDelivery, SendingIdentity, SendingIdentityKind
from app.integration.models.notification import IntegrationNotification
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef

_connection = table_of(IntegrationConnection)


def _by_connection(table):
    return lambda scope: table.c.connection_id.in_(
        select(_connection.c.id).where(_connection.c.tenant_id == scope.dealership_id)
    )


def _tenant(model, **kwargs) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id, **kwargs)


def _business_mailboxes() -> ExportedTable:
    table = table_of(SendingIdentity)
    return ExportedTable(
        table=table,
        scope=lambda scope: (table.c.tenant_id == scope.dealership_id)
        & (table.c.kind == SendingIdentityKind.BUSINESS_MAILBOX.value),
    )


class IntegrationExporter(TableExporter):
    context = "integration"

    def exported_tables(self) -> Sequence[ExportedTable]:
        entitlement = table_of(IntegrationEntitlement)
        secret_ref = table_of(IntegrationSecretRef)
        return (
            _tenant(IntegrationConnection),
            # KAN-227: the Betrieb's adaptations of the AI prompts (Nexotec's
            # seeds have no Betrieb and are not its data).
            _tenant(IntegrationAiPrompt),
            ExportedTable(table=entitlement, scope=_by_connection(entitlement), marks_write=False),
            ExportedTable(
                table=secret_ref,
                scope=_by_connection(secret_ref),
                in_archive=False,
                exclusion_reason="A write-only reference into the secrets manager (ADR-024); never returned.",
            ),
            # The calls and notices happen without the Betrieb writing anything.
            # KAN-215 (Anto's D2 A, 2026-11-15): the Betrieb's shared mailbox and
            # every delivery (sender, recipients, subject, date, status) are its
            # data; a colleague's own mailbox address is not (D-A-15) — those
            # rows stay out of the archive.
            _business_mailboxes(),
            _tenant(MessageDelivery),
            _tenant(
                MailLinkState,
                in_archive=False,
                exclusion_reason="A pending mailbox link, valid ten minutes; a hash of a single-use state only.",
                marks_write=False,
            ),
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
        catalogue = "Nexotec's provider catalogue, the same for every business."
        return {
            IntegrationProvider.__tablename__: catalogue,
            IntegrationProviderCaption.__tablename__: catalogue,
            IntegrationProviderCaptionFetch.__tablename__: catalogue,
        }

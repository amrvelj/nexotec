"""Integration's outbound cross-context references (WP-6 PR-1). Everything
here is read-only — see app.core.reconciliation for the mechanism.
Registered in app/reconciliation_runner.py's own `_JOBS` list. That
composition root itself has no scheduling trigger anywhere in this repo
today (a pre-existing, orthogonal gap predating this package — flagged,
not fixed here) — being registered means it runs whenever `run_all()` is
next actually invoked, same as every other context's own job.
"""

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.call_payload import IntegrationCallPayload
from app.integration.models.caption import IntegrationProviderCaption
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.notification import IntegrationNotification
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import IntegrationSecretRef
from app.platform.public import Dealership, actor_reference_checks

CONTEXT = "integration"

# Every Integration table with created_by / updated_by: each must name a
# platform user, or be null — the gateway and the daily jobs write null
# (KAN-243; see app.platform.public.actor_reference_checks).
ACTOR_TABLES = (
    IntegrationProvider,
    IntegrationConnection,
    IntegrationSecretRef,
    IntegrationEntitlement,
    IntegrationCallLog,
    IntegrationCallPayload,
    IntegrationNotification,
    IntegrationProviderCaption,  # KAN-547: written by Test connection, never by a user — null actors
)


def _not_a_deleted_connection() -> ColumnElement[bool]:
    """A notification outlives its connection (KAN-494, Anto 2026-10-29,
    option A — the call log's rule, KAN-470): an id whose delete is on the
    audit trail (`connections.delete_connection` records it) is expected,
    not drift. An id that vanished without a recorded delete still is.
    """

    deleted = select(AuditEvent.entity_id).where(
        AuditEvent.entity_type == "integration_connection", AuditEvent.action == "delete"
    )
    return IntegrationNotification.connection_id.not_in(deleted)


CHECKS = [
    ReferenceCheck(
        label="integration_connection.tenant_id -> dealership.id",
        source_model=IntegrationConnection,
        source_row_id_column=IntegrationConnection.id,
        source_fk_column=IntegrationConnection.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
        nullable=True,  # platform-scoped connections have no tenant
    ),
    ReferenceCheck(
        label="integration_secret_ref.connection_id -> integration_connection.id",
        source_model=IntegrationSecretRef,
        source_row_id_column=IntegrationSecretRef.id,
        source_fk_column=IntegrationSecretRef.connection_id,
        target_model=IntegrationConnection,
        target_id_column=IntegrationConnection.id,
    ),
    ReferenceCheck(
        label="integration_entitlement.connection_id -> integration_connection.id",
        source_model=IntegrationEntitlement,
        source_row_id_column=IntegrationEntitlement.id,
        source_fk_column=IntegrationEntitlement.connection_id,
        target_model=IntegrationConnection,
        target_id_column=IntegrationConnection.id,
    ),
    # integration_call_log.connection_id is deliberately not checked: the
    # call log outlives a deleted connection until ADR-024 retention purges
    # it (KAN-470), so a row naming no connection is expected, not drift.
    # --- WP-6 PR-6 additions ---
    ReferenceCheck(
        label="integration_call_payload.call_log_id -> integration_call_log.id",
        source_model=IntegrationCallPayload,
        source_row_id_column=IntegrationCallPayload.id,
        source_fk_column=IntegrationCallPayload.call_log_id,
        target_model=IntegrationCallLog,
        target_id_column=IntegrationCallLog.id,
    ),
    ReferenceCheck(
        label="integration_notification.connection_id -> integration_connection.id",
        source_model=IntegrationNotification,
        source_row_id_column=IntegrationNotification.id,
        source_fk_column=IntegrationNotification.connection_id,
        target_model=IntegrationConnection,
        target_id_column=IntegrationConnection.id,
        nullable=True,  # the daily support digest has no single connection
        source_where=_not_a_deleted_connection,
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

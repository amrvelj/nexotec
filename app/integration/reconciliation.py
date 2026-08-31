"""Integration's outbound cross-context references (WP-6 PR-1). Everything
here is read-only — see app.core.reconciliation for the mechanism.
Registered in app/reconciliation_runner.py's own `_JOBS` list. That
composition root itself has no scheduling trigger anywhere in this repo
today (a pre-existing, orthogonal gap predating this package — flagged,
not fixed here) — being registered means it runs whenever `run_all()` is
next actually invoked, same as every other context's own job.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.integration.models.connection import IntegrationConnection
from app.integration.models.entitlement import IntegrationEntitlement
from app.integration.models.secret_ref import IntegrationSecretRef
from app.platform.public import Dealership

CONTEXT = "integration"

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
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

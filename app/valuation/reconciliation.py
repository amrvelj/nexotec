"""Valuation's outbound cross-context references (WP-8 PR-5). Everything
here is read-only — see app.core.reconciliation for the mechanism.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.public import Customer
from app.platform.public import Dealership
from app.valuation.models.valuation import Valuation
from app.vehicle.public import VehicleMdm

CONTEXT = "valuation"

CHECKS = [
    ReferenceCheck(
        label="valuation.tenant_id -> dealership.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="valuation.vehicle_id -> vehicle_mdm.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=True,  # creatable with no vehicle in the register (confirmed live)
    ),
    ReferenceCheck(
        label="valuation.customer_id -> customer.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,  # "Ohne Kunde" (confirmed live filter chip)
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

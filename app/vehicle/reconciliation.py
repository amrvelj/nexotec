"""Vehicle's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.platform.public import Dealership
from app.sales.public import Transaction
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent

CONTEXT = "vehicle"

CHECKS = [
    ReferenceCheck(
        label="vehicle.current_custodian_partner_id -> dealership.id",
        source_model=Vehicle,
        source_row_id_column=Vehicle.id,
        source_fk_column=Vehicle.current_custodian_partner_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="vehicle_custody_event.partner_id -> dealership.id",
        source_model=VehicleCustodyEvent,
        source_row_id_column=VehicleCustodyEvent.id,
        source_fk_column=VehicleCustodyEvent.partner_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_custody_event.transaction_id -> transaction.id",
        source_model=VehicleCustodyEvent,
        source_row_id_column=VehicleCustodyEvent.id,
        source_fk_column=VehicleCustodyEvent.transaction_id,
        target_model=Transaction,
        target_id_column=Transaction.id,
        nullable=True,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

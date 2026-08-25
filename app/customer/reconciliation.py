"""Customer's outbound cross-context references (PR-2). Everything here is
read-only — see app.core.reconciliation for the mechanism.
"""

from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.models.customer import (
    Customer,
    CustomerEmail,
    CustomerExternalId,
    CustomerNumberSequence,
    CustomerPhone,
)
from app.customer.models.vehicle_party import VehicleParty
from app.platform.public import Dealership
from app.vehicle.public import Vehicle

CONTEXT = "customer"

CHECKS = [
    ReferenceCheck(
        label="vehicle_party.vehicle_id -> vehicle.id",
        source_model=VehicleParty,
        source_row_id_column=VehicleParty.id,
        source_fk_column=VehicleParty.vehicle_id,
        target_model=Vehicle,
        target_id_column=Vehicle.id,
    ),
    ReferenceCheck(
        label="customer.tenant_id -> dealership.id",
        source_model=Customer,
        source_row_id_column=Customer.id,
        source_fk_column=Customer.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="customer_number_sequence.tenant_id -> dealership.id",
        source_model=CustomerNumberSequence,
        source_row_id_column=CustomerNumberSequence.tenant_id,
        source_fk_column=CustomerNumberSequence.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="customer_phone.tenant_id -> dealership.id",
        source_model=CustomerPhone,
        source_row_id_column=CustomerPhone.id,
        source_fk_column=CustomerPhone.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="customer_email.tenant_id -> dealership.id",
        source_model=CustomerEmail,
        source_row_id_column=CustomerEmail.id,
        source_fk_column=CustomerEmail.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="customer_external_id.tenant_id -> dealership.id",
        source_model=CustomerExternalId,
        source_row_id_column=CustomerExternalId.id,
        source_fk_column=CustomerExternalId.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

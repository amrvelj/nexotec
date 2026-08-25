"""Customer's outbound cross-context references (PR-2, repointed to
DealerGroup in WP-3 PR-2, ADR-014 — Customer and its child collections moved
from dealership-scoped to group-scoped). Everything here is read-only — see
app.core.reconciliation for the mechanism.
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
from app.platform.public import DealerGroup
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
        label="customer.group_id -> dealer_group.id",
        source_model=Customer,
        source_row_id_column=Customer.id,
        source_fk_column=Customer.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_number_sequence.group_id -> dealer_group.id",
        source_model=CustomerNumberSequence,
        source_row_id_column=CustomerNumberSequence.group_id,
        source_fk_column=CustomerNumberSequence.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_phone.group_id -> dealer_group.id",
        source_model=CustomerPhone,
        source_row_id_column=CustomerPhone.id,
        source_fk_column=CustomerPhone.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_email.group_id -> dealer_group.id",
        source_model=CustomerEmail,
        source_row_id_column=CustomerEmail.id,
        source_fk_column=CustomerEmail.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    ReferenceCheck(
        label="customer_external_id.group_id -> dealer_group.id",
        source_model=CustomerExternalId,
        source_row_id_column=CustomerExternalId.id,
        source_fk_column=CustomerExternalId.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

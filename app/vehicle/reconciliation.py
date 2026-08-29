"""Vehicle's outbound cross-context references. Everything here is
read-only — see app.core.reconciliation for the mechanism.

WP-5 PR-7 adds the ReferenceChecks for every cross-context GUID column
PR-1 through PR-6 introduced (plates, odometer, accessories, dealer
plates, the new custody log) — CLAUDE.md rule 10 ("distributed integrity
is monitored, not assumed... in v1, never later") applies to those exactly
as it does to the pre-existing ones below, and they had no coverage before
this PR.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.reconciliation import ReconciliationRun, ReferenceCheck, run_reconciliation
from app.customer.public import VehicleParty
from app.platform.public import Dealership
from app.sales.public import Transaction
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate
from app.vehicle.models.vehicle import Vehicle, VehicleCustodyEvent
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as VehicleMdmCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm

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
    # --- PR-7 additions: every cross-context GUID column PR-1 through
    # PR-6 introduced, none of which had a check before this PR. ---
    ReferenceCheck(
        label="vehicle_plate.recording_tenant_id -> dealership.id",
        source_model=VehiclePlate,
        source_row_id_column=VehiclePlate.id,
        source_fk_column=VehiclePlate.recording_tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_odometer_reading.recording_tenant_id -> dealership.id",
        source_model=VehicleOdometerReading,
        source_row_id_column=VehicleOdometerReading.id,
        source_fk_column=VehicleOdometerReading.recording_tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_accessory.recording_tenant_id -> dealership.id",
        source_model=VehicleAccessory,
        source_row_id_column=VehicleAccessory.id,
        source_fk_column=VehicleAccessory.recording_tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_mdm_custody_event.partner_id -> dealership.id",
        source_model=VehicleMdmCustodyEvent,
        source_row_id_column=VehicleMdmCustodyEvent.id,
        source_fk_column=VehicleMdmCustodyEvent.partner_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_mdm_custody_event.transaction_id -> transaction.id",
        source_model=VehicleMdmCustodyEvent,
        source_row_id_column=VehicleMdmCustodyEvent.id,
        source_fk_column=VehicleMdmCustodyEvent.transaction_id,
        target_model=Transaction,
        target_id_column=Transaction.id,
        nullable=True,
    ),
    ReferenceCheck(
        label="vehicle_dealer_plate.tenant_id -> dealership.id",
        source_model=DealerPlate,
        source_row_id_column=DealerPlate.id,
        source_fk_column=DealerPlate.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_dealer_plate_assignment.tenant_id -> dealership.id",
        source_model=DealerPlateAssignment,
        source_row_id_column=DealerPlateAssignment.id,
        source_fk_column=DealerPlateAssignment.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)


def count_unrepointed_legacy_vehicle_party_references(db: Session) -> int:
    """WP-5 PR-7's own health check, separate from the generic
    ReferenceCheck framework above (which asks "does this FK resolve to
    any row", not "is this FK still using an id that has a known
    newer replacement"). Every VehicleMdm with migrated_from_legacy_
    vehicle_id set has a corresponding old vehicle.id; a VehicleParty row
    still pointing at that OLD id (rather than the new VehicleMdm.id) is
    exactly the kind of reference the exit criterion's "zero unresolved
    references for seven consecutive nights" is checking for before the
    old table can ever be retired — the old table itself stays perfectly
    readable in the meantime (it is never dropped by this check), so
    nothing is actually broken by a nonzero count; it's the retirement
    gate, not an alarm on its own.
    """

    legacy_ids = list(db.scalars(select(VehicleMdm.migrated_from_legacy_vehicle_id).where(
        VehicleMdm.migrated_from_legacy_vehicle_id.is_not(None)
    )).all())
    if not legacy_ids:
        return 0
    return db.scalar(
        select(func.count()).select_from(VehicleParty).where(VehicleParty.vehicle_id.in_(legacy_ids))
    ) or 0

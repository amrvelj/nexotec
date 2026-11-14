"""Vehicle's outbound cross-context references. Everything here is
read-only — see app.core.reconciliation for the mechanism.

WP-5 PR-7 adds the ReferenceChecks for every cross-context GUID column
PR-1 through PR-6 introduced (plates, odometer, accessories, dealer
plates, the new custody log) — CLAUDE.md rule 10 ("distributed integrity
is monitored, not assumed... in v1, never later") applies to those exactly
as it does to the pre-existing ones below, and they had no coverage before
this PR.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.inventory.public import (
    held_stock_item_check,
    stock_item_differs_from_holding,
    stock_item_reference_check,
)
from app.platform.public import DealerGroup, Dealership, actor_reference_checks
from app.vehicle.models.catalogue import (
    Brand,
    ModelGroup,
    ModelVariant,
    TypeApproval,
    VariantOption,
    VariantOptionEquipmentFeature,
    VariantOptionRelation,
    VariantPrice,
    VariantTypeApproval,
)
from app.vehicle.models.catalogue_mirror import ColourCache, ImageRef, TyreSpecCache, VariantDetailState
from app.vehicle.models.configuration import (
    VehicleConfiguration,
    VehicleConfigurationOption,
    VehicleConfigurationOptionFeature,
)
from app.vehicle.models.energy_rating import ModelVariantEnergyLabel, ModelVariantEnergyRating
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment, VehiclePlate
from app.vehicle.models.provider import (
    MappingGap,
    ProviderCodeMap,
    ProviderEntityRef,
    VariantOptionProviderCode,
    VariantProviderCode,
)
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleOdometerReading
from app.vehicle.models.vehicle_history import VehicleCustodyEvent as VehicleMdmCustodyEvent
from app.vehicle.models.vehicle_mdm import VehicleMdm

CONTEXT = "vehicle"

# Every Vehicle table with created_by / updated_by: each must name a platform
# user, or be null — the catalogue sync and the provider mirror write null
# (KAN-243; see app.platform.public.actor_reference_checks). The custody
# log has created_by only. vehicle_model_variant_type_approval has a
# composite key; its findings name the row by model_variant_id.
ACTOR_TABLES = (
    VehicleMdm,
    VehicleMdmCustodyEvent,
    VehicleOdometerReading,
    VehicleAccessory,
    VehiclePlate,
    DealerPlate,
    DealerPlateAssignment,
    Brand,
    ModelGroup,
    ModelVariant,
    TypeApproval,
    VariantTypeApproval,
    VariantOption,
    VariantOptionEquipmentFeature,
    VariantOptionRelation,
    VariantPrice,
    ModelVariantEnergyRating,
    ModelVariantEnergyLabel,
    ColourCache,
    TyreSpecCache,
    ImageRef,
    VariantDetailState,
    VehicleConfiguration,
    VehicleConfigurationOption,
    VehicleConfigurationOptionFeature,
    ProviderCodeMap,
    ProviderEntityRef,
    VariantProviderCode,
    VariantOptionProviderCode,
    MappingGap,
)

# VehicleStockHolding (KAN-667) carries no actor column: consumers and a
# backfill write it, never a user.

# KAN-667: how long one of Stock's facts may be in flight before a projection
# row that disagrees with its stock item is a finding (KAN-630's grace).
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)


def _stock_holding_differs_from_its_stock_item() -> StateCheck:
    """KAN-667 (rule 10): Vehicle's projection says something else than the
    stock item it mirrors — another Betrieb, another record for an item still
    in stock, or in stock where the item left (or the reverse) — although the
    item last changed over an hour ago: one of Stock's facts was missed
    (dead-lettered, or the consumer is stuck). The identity PATCH would then
    refuse a VIN change it should allow, or allow one it should refuse. A
    stock item that no longer exists is the reference check's finding."""

    return StateCheck(
        label="vehicle_stock_holding differs from its stock_item",
        source_model=VehicleStockHolding,
        source_row_id_column=VehicleStockHolding.id,
        where=lambda: stock_item_differs_from_holding(
            VehicleStockHolding.stock_item_id,
            tenant_id=VehicleStockHolding.tenant_id,
            left_stock_at=VehicleStockHolding.left_stock_at,
            vehicle_id=VehicleStockHolding.vehicle_id,
            changed_before=utcnow() - _OUTBOX_LAG_GRACE,
        ).correlate(VehicleStockHolding),
    )


def _held_stock_item_missing_from_the_projection() -> StateCheck:
    """KAN-667 (rule 10): a stock item that holds a vehicle-mdm record and
    has not left stock, unchanged for an hour, that the projection does not
    hold — its `vin_assigned` fact was missed, or the projection backfill has
    not run since the deploy. Its VIN could be changed on the Vehicle 360
    alone. The finding names the stock item."""

    return held_stock_item_check(
        label="stock item holding a record missing from vehicle_stock_holding",
        grace=_OUTBOX_LAG_GRACE,
        holding=lambda stock_item_id, tenant_id: select(VehicleStockHolding.id).where(
            VehicleStockHolding.stock_item_id == stock_item_id,
            VehicleStockHolding.tenant_id == tenant_id,
            VehicleStockHolding.vehicle_id.is_not(None),
        ),
    )


CHECKS: list[ReferenceCheck | StateCheck] = [
    # --- KAN-667: Vehicle's projection of the records a Betrieb holds in stock.
    _stock_holding_differs_from_its_stock_item(),
    _held_stock_item_missing_from_the_projection(),
    stock_item_reference_check(
        label="vehicle_stock_holding.stock_item_id -> stock_item.id",
        source_model=VehicleStockHolding,
        source_row_id_column=VehicleStockHolding.id,
        source_fk_column=VehicleStockHolding.stock_item_id,
    ),
    ReferenceCheck(
        label="vehicle_stock_holding.tenant_id -> dealership.id",
        source_model=VehicleStockHolding,
        source_row_id_column=VehicleStockHolding.id,
        source_fk_column=VehicleStockHolding.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
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
    # KAN-467 (ADR-039 as amended): the register a plate is kept in is its
    # dealer group's. The group must exist, and it must be the group of the
    # Betrieb that recorded the plate — a session minted for a Betrieb carries
    # that Betrieb's group (ADR-106), so a difference means a write named the
    # wrong register.
    ReferenceCheck(
        label="vehicle_plate.group_id -> dealer_group.id",
        source_model=VehiclePlate,
        source_row_id_column=VehiclePlate.id,
        source_fk_column=VehiclePlate.group_id,
        target_model=DealerGroup,
        target_id_column=DealerGroup.id,
    ),
    StateCheck(
        label="vehicle_plate kept in a group that is not its recording Betrieb's",
        source_model=VehiclePlate,
        source_row_id_column=VehiclePlate.id,
        where=lambda: (
            select(Dealership.id)
            .where(Dealership.id == VehiclePlate.recording_tenant_id)
            .where(Dealership.dealer_group_id.is_distinct_from(VehiclePlate.group_id))
            .correlate(VehiclePlate)
            .exists()
        ),
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
    # --- WP-6 PR-4: the catalogue-mirror tenant-partitioning retrofit and
    # its new tables, all tenant-scoped, none had a check before this PR.
    ReferenceCheck(
        label="vehicle_variant_option.tenant_id -> dealership.id",
        source_model=VariantOption,
        source_row_id_column=VariantOption.id,
        source_fk_column=VariantOption.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    # KAN-250: an option's raw SuchCode list is tenant-scoped like the option itself.
    ReferenceCheck(
        label="vehicle_variant_option_provider_code.tenant_id -> dealership.id",
        source_model=VariantOptionProviderCode,
        source_row_id_column=VariantOptionProviderCode.id,
        source_fk_column=VariantOptionProviderCode.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_colour_cache.tenant_id -> dealership.id",
        source_model=ColourCache,
        source_row_id_column=ColourCache.id,
        source_fk_column=ColourCache.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_tyre_spec_cache.tenant_id -> dealership.id",
        source_model=TyreSpecCache,
        source_row_id_column=TyreSpecCache.id,
        source_fk_column=TyreSpecCache.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_image_ref.tenant_id -> dealership.id",
        source_model=ImageRef,
        source_row_id_column=ImageRef.id,
        source_fk_column=ImageRef.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="vehicle_variant_detail_state.tenant_id -> dealership.id",
        source_model=VariantDetailState,
        source_row_id_column=VariantDetailState.id,
        source_fk_column=VariantDetailState.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)


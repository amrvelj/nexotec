"""Vehicles' export declaration (KAN-218; FR-A-44).

In the file: the Betrieb's configurations, dealer plates and their
assignments, its stock holdings, the odometer readings and accessories it
recorded, and the vehicle records all of these name. Never the Kontrollschild
register: it is not listed or exported (ADR-039; `plate.register_export_table`).

Out of the file, with the reason in `schema.json`: Nexotec's canonical
catalogue (the same for every Betrieb), the per-Betrieb provider catalogue
mirror (the provider's licensed data, re-fetched on demand — not the
Betrieb's record), the registry-lookup cache (30 days, KAN-42) and the
custody history of the physical vehicle (recorded platform-wide).

At the deletion after the retention period the Betrieb's own rows go. The
physical vehicle and the facts recorded about it (odometer, accessories) stay:
a vehicle is platform-wide master data, not the Betrieb's. The plate register
is group-owned (KAN-467): it goes once no sister Betrieb holds data.
"""

from collections.abc import Mapping, Sequence

from sqlalchemy import select, union

from app.core.export import ExportedTable, ExportScope, TableExporter, table_of
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
from app.vehicle.models.plate import DealerPlate, DealerPlateAssignment
from app.vehicle.models.plate_lookup_cache import PlateLookupCacheEntry
from app.vehicle.models.provider import (
    MappingGap,
    ProviderCodeMap,
    ProviderEntityRef,
    VariantOptionProviderCode,
    VariantProviderCode,
)
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_history import VehicleAccessory, VehicleCustodyEvent, VehicleOdometerReading
from app.vehicle.models.vehicle_mdm import VehicleMdm, VehicleNumberSequence
from app.vehicle.services.plate import register_export_table

_holding = table_of(VehicleStockHolding)
_assignment = table_of(DealerPlateAssignment)
_odometer = table_of(VehicleOdometerReading)
_accessory = table_of(VehicleAccessory)
_vehicle = table_of(VehicleMdm)

_MIRROR = "The provider's licensed catalogue data mirrored for the Betrieb, re-fetched on demand."
_PHYSICAL = "The physical vehicle is platform-wide master data, not the Betrieb's (vehicle-mdm)."


def _tenant(model, **kwargs) -> ExportedTable:
    table = table_of(model)
    return ExportedTable(table=table, scope=lambda scope: table.c.tenant_id == scope.dealership_id, **kwargs)


def _mirror(model) -> ExportedTable:
    return _tenant(model, in_archive=False, exclusion_reason=_MIRROR, marks_write=False)


def _named_vehicle_ids(scope: ExportScope):
    """Every vehicle record the Betrieb's own rows name."""

    tenant = scope.dealership_id
    return union(
        select(_holding.c.vehicle_id).where(_holding.c.tenant_id == tenant),
        select(_assignment.c.vehicle_id).where(_assignment.c.tenant_id == tenant),
        select(_odometer.c.vehicle_id).where(_odometer.c.recording_tenant_id == tenant),
        select(_accessory.c.vehicle_id).where(_accessory.c.recording_tenant_id == tenant),
    )


class VehicleExporter(TableExporter):
    context = "vehicle"

    def exported_tables(self) -> Sequence[ExportedTable]:
        configuration_option = table_of(VehicleConfigurationOption)
        return (
            _tenant(VehicleConfiguration),
            _tenant(VehicleConfigurationOption),
            _tenant(VehicleConfigurationOptionFeature, depends_on=(configuration_option,)),
            _tenant(DealerPlate),
            _tenant(DealerPlateAssignment),
            _tenant(VehicleStockHolding),
            register_export_table(),
            ExportedTable(
                table=_odometer,
                scope=lambda scope: _odometer.c.recording_tenant_id == scope.dealership_id,
                erase=False,
                keep_reason=_PHYSICAL,
            ),
            ExportedTable(
                table=_accessory,
                scope=lambda scope: _accessory.c.recording_tenant_id == scope.dealership_id,
                erase=False,
                keep_reason=_PHYSICAL,
            ),
            ExportedTable(
                table=_vehicle,
                scope=lambda scope: _vehicle.c.id.in_(_named_vehicle_ids(scope)),
                erase=False,
                keep_reason=_PHYSICAL,
                # Other Betriebe write the shared record too.
                marks_write=False,
            ),
            _mirror(VariantOption),
            _mirror(VariantOptionEquipmentFeature),
            _mirror(VariantOptionRelation),
            _mirror(VariantOptionProviderCode),
            _mirror(ColourCache),
            _mirror(ImageRef),
            _mirror(TyreSpecCache),
            _mirror(VariantDetailState),
            _tenant(
                PlateLookupCacheEntry,
                in_archive=False,
                exclusion_reason="A 30-day cache of registry lookups (KAN-42); no record of the Betrieb's.",
                marks_write=False,
            ),
        )

    def excluded_tables(self) -> Mapping[str, str]:
        catalogue = "Nexotec's canonical vehicle catalogue, the same for every Betrieb."
        return {
            **{
                model.__tablename__: catalogue
                for model in (
                    Brand,
                    ModelGroup,
                    ModelVariant,
                    TypeApproval,
                    VariantTypeApproval,
                    VariantPrice,
                    ModelVariantEnergyRating,
                    ModelVariantEnergyLabel,
                    ProviderCodeMap,
                    ProviderEntityRef,
                    VariantProviderCode,
                    MappingGap,
                )
            },
            VehicleCustodyEvent.__tablename__: "The physical vehicle's custody history, recorded platform-wide.",
            VehicleNumberSequence.__tablename__: "Nexotec's platform-wide vehicle-number counter.",
        }

"""What another context sees of a valuation (KAN-123 step 2, ADR-162).

`app.valuation.public` hands out these plain values, never the mapped
`Valuation` row: with a row in hand, Sales or Stock could select, join, change
and flush valuation's table while the import-linter passes, since the import
went through `public`. Valuation is the single writer (ADR-048 as amended,
ADR-066) and tenant-private (ADR-029); the contract a remote service would offer
(ADR-015) is a value.

`ValuationSummary` carries exactly what the two readers take: Sales' trade-in
(`sales/services/trade_in.py`: the id and number, the car it names, its
configuration, the figure) and Stock's `valuationRef` copy
(`inventory/services/pipeline.py`: id, figure, when, source). Valuation's own
code keeps using `services/valuation.py`'s functions of the same names, which
return the rows.
"""

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.services import valuation as valuation_service


@dataclasses.dataclass(frozen=True)
class ValuationSummary:
    """One valuation of one Betrieb, as another context reads it."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    valuation_number: str
    vehicle_id: uuid.UUID | None
    vehicle_vin: str | None
    vehicle_make: str | None
    vehicle_model: str | None
    vehicle_trim: str | None
    configuration_id: uuid.UUID | None
    configuration_label: str | None
    final_offer: Decimal
    source: ValuationSource
    created_at: dt.datetime


def summarise(row: Valuation) -> ValuationSummary:
    return ValuationSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        valuation_number=row.valuation_number,
        vehicle_id=row.vehicle_id,
        vehicle_vin=row.vehicle_vin,
        vehicle_make=row.vehicle_make,
        vehicle_model=row.vehicle_model,
        vehicle_trim=row.vehicle_trim,
        configuration_id=row.configuration_id,
        configuration_label=row.configuration_label,
        final_offer=row.final_offer,
        source=row.source,
        created_at=row.created_at,
    )


def get_valuation_or_404(db: Session, tenant_id: uuid.UUID, valuation_id: uuid.UUID) -> ValuationSummary:
    """The Betrieb's valuation, or 404 — another Betrieb's reads as missing (rule 7)."""

    return summarise(valuation_service.get_valuation_or_404(db, tenant_id, valuation_id))


def list_valid_valuations_for_vehicle(
    db: Session, *, tenant_id: uuid.UUID, vehicle_id: uuid.UUID
) -> list[ValuationSummary]:
    """FR-S-08: the Betrieb's valid valuations of the vehicle-mdm record, newest first."""

    rows = valuation_service.list_valid_valuations_for_vehicle(db, tenant_id=tenant_id, vehicle_id=vehicle_id)
    return [summarise(row) for row in rows]


def list_valid_valuations_for_vin(db: Session, *, tenant_id: uuid.UUID, vin: str) -> list[ValuationSummary]:
    """FR-S-08 for a car not in the register (KAN-189): by VIN, compared normalised, newest first."""

    return [summarise(row) for row in valuation_service.list_valid_valuations_for_vin(db, tenant_id=tenant_id, vin=vin)]

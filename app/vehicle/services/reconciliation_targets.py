"""What another context's nightly reconciliation checks against Vehicle's
`vehicle_mdm` and `vehicle_configuration` tables (rule 10; ADR-107), built
here so the mapped `VehicleMdm` and `VehicleConfiguration` classes never
leave the vehicle context (KAN-123 step 6, ADR-162).

Contacts (vehicle parties), Sales (offers, contracts, deals), Valuation and
Stock hold vehicle-mdm and configuration ids (the three-column pattern).
Their `reconciliation.py` asks:

- `vehicle_reference_check` / `configuration_reference_check` — the id names
  a record (an orphan otherwise).
- `vehicle_is_merged` — an EXISTS: the id names a record merged into another
  (`merged_into_vehicle_id`), optionally only one whose merge is older than a
  given time.
- `vehicle_vin_distinct_from` / `vehicle_vin_differs` — an EXISTS: the record
  carries another VIN (`IS DISTINCT FROM`, and plain `!=` for a VIN the
  source may store in another spelling, compared upper-cased by its caller).
- `configuration_exists` — an EXISTS: the id names a configuration, in a
  given Betrieb when one is given.
- `configuration_untaken` — an EXISTS: the configuration is one no host has
  taken yet (`first_taken_at` null).
- `configuration_vin_distinct_from` — an EXISTS: the configuration carries a
  VIN, and another one.

Builders only: each reads nothing itself — the reconciliation run executes
it. Each EXISTS correlates to `correlate` when it is given, as the checks it
replaced did, so the SQL is the one they ran. An EXISTS over Vehicle's
tables embedded in a live query would read across out of
`test_no_cross_context_orm_query.py`'s sight, so they are confined to the
reconciliation modules
(`tests/architecture/test_vehicle_predicates_only_in_reconciliation.py`).
"""

import datetime as dt
from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, Exists, Select, select
from sqlalchemy.orm import DeclarativeBase, InstrumentedAttribute

from app.core.reconciliation import ReferenceCheck
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm

_Column = ColumnElement[Any] | InstrumentedAttribute[Any]


def _exists(query: Select[Any], correlate: type[DeclarativeBase] | None) -> Exists:
    return (query.correlate(correlate) if correlate is not None else query).exists()


def vehicle_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `vehicle_mdm` row."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=nullable,
        source_where=source_where,
    )


def configuration_reference_check(
    *,
    label: str,
    source_model: type[DeclarativeBase],
    source_row_id_column: InstrumentedAttribute[Any],
    source_fk_column: InstrumentedAttribute[Any],
    nullable: bool = False,
    source_where: Callable[[], ColumnElement[bool]] | None = None,
) -> ReferenceCheck:
    """`source_fk_column` names a `vehicle_configuration` row."""

    return ReferenceCheck(
        label=label,
        source_model=source_model,
        source_row_id_column=source_row_id_column,
        source_fk_column=source_fk_column,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=nullable,
        source_where=source_where,
    )


def vehicle_is_merged(
    vehicle_id: _Column,
    *,
    updated_before: dt.datetime | None = None,
    correlate: type[DeclarativeBase] | None = None,
) -> Exists:
    """The record `vehicle_id` names is merged into another — and was last
    written (the merge stamps it) before `updated_before` when one is given."""

    conditions = [VehicleMdm.id == vehicle_id, VehicleMdm.merged_into_vehicle_id.is_not(None)]
    if updated_before is not None:
        conditions.append(VehicleMdm.updated_at < updated_before)
    return _exists(select(VehicleMdm.id).where(*conditions), correlate)


def vehicle_vin_distinct_from(
    vehicle_id: _Column, vin: _Column, *, correlate: type[DeclarativeBase] | None = None
) -> Exists:
    """The record `vehicle_id` names carries a VIN `IS DISTINCT FROM` `vin`."""

    return _exists(
        select(VehicleMdm.id).where(VehicleMdm.id == vehicle_id, VehicleMdm.vin.is_distinct_from(vin)), correlate
    )


def vehicle_vin_differs(
    vehicle_id: _Column,
    vin: _Column,
    *,
    only_where: ColumnElement[bool] | None = None,
    correlate: type[DeclarativeBase] | None = None,
) -> Exists:
    """The record `vehicle_id` names carries a VIN `!=` `vin` (NULL compares
    as nothing), with the caller's own `only_where` condition in the same
    subquery when one is given."""

    conditions: list[ColumnElement[bool]] = [VehicleMdm.id == vehicle_id]
    if only_where is not None:
        conditions.append(only_where)
    conditions.append(VehicleMdm.vin != vin)
    return _exists(select(VehicleMdm.id).where(*conditions), correlate)


def configuration_exists(
    configuration_id: _Column, *, tenant_id: _Column | None = None, correlate: type[DeclarativeBase] | None = None
) -> Exists:
    """A configuration with this id exists — in `tenant_id`'s Betrieb when one is given."""

    conditions = [VehicleConfiguration.id == configuration_id]
    if tenant_id is not None:
        conditions.append(VehicleConfiguration.tenant_id == tenant_id)
    return _exists(select(VehicleConfiguration.id).where(*conditions), correlate)


def configuration_untaken(configuration_id: _Column, *, correlate: type[DeclarativeBase] | None = None) -> Exists:
    """The configuration `configuration_id` names has not been stamped as taken by a host."""

    return _exists(
        select(VehicleConfiguration.id).where(
            VehicleConfiguration.id == configuration_id, VehicleConfiguration.first_taken_at.is_(None)
        ),
        correlate,
    )


def configuration_vin_distinct_from(
    configuration_id: _Column, vin: _Column, *, correlate: type[DeclarativeBase] | None = None
) -> Exists:
    """The configuration `configuration_id` names carries a VIN, and one
    `IS DISTINCT FROM` `vin`."""

    return _exists(
        select(VehicleConfiguration.id).where(
            VehicleConfiguration.id == configuration_id,
            VehicleConfiguration.vin.is_not(None),
            VehicleConfiguration.vin.is_distinct_from(vin),
        ),
        correlate,
    )

"""Catalogue browse and facet search (C-B / KAN-40, FR-C-01).

Drill-down (brand → model group → variant), cross-variant filtering, and
mirror-derived facets — **the catalogue mirror is the only source**. This
module must never reach a provider: it does not import `app.integration`
or `app.vehicle.services.catalogue_sync`'s provider path, and
`tests/architecture/test_catalogue_browse_makes_no_provider_call.py`
enforces that (source scan + a runtime guard that fails if
`call_capability` is ever entered).

Facets (`compute_facets`) are `GROUP BY` / `min`-`max` aggregates over the
in-scope mirror rows — the "offer only values that actually exist for the
brand or model in view" rule, computed from our own data and guaranteed
consistent with what is browsable. The provider's pre-aggregated
`FzgWerteGruppiert` is a later optimisation (C-0), not a dependency.

Production-year filtering follows the configurator mode: `build` defaults
to *currently in production* (`model_year_to IS NULL`), `record` shows
everything (PRD `NurNeue` — "defaulted on in build, off in record"). An
explicit `in_production` filter overrides the default either way.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import get_settings
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.vehicle.models.catalogue import Brand, ModelGroup, ModelVariant, VariantTypeApproval
from app.vehicle.schemas.catalogue import CatalogueBrowseMode
from app.vehicle.services.catalogue_entitlements import get_catalogue_entitlements

# API field name → the ModelVariant column it filters / facets on. The
# coded ones resolve to a canonical reference_value.value_code string
# (never a raw provider code — resolved upstream in catalogue_sync).
CODED_FACET_COLUMNS: dict[str, Any] = {
    "vehicleKind": ModelVariant.vehicle_kind,
    "fuelType": ModelVariant.fuel_type,
    "bodyStyle": ModelVariant.body_style,
    "drivetrain": ModelVariant.drivetrain,
    "transmission": ModelVariant.transmission,
    "engineCycle": ModelVariant.engine_cycle,
    "vehicleClass": ModelVariant.vehicle_class,
    "emissionStandard": ModelVariant.emission_standard,
}

NUMERIC_FACET_COLUMNS: dict[str, Any] = {
    "ps": ModelVariant.ps,
    "kw": ModelVariant.kw,
    "displacementCcm": ModelVariant.displacement_ccm,
    "doors": ModelVariant.doors,
    "seats": ModelVariant.seats,
    "co2Gkm": ModelVariant.co2_gkm,
    "modelYearFrom": ModelVariant.model_year_from,
    "basePrice": ModelVariant.base_price,
}


@dataclass(frozen=True)
class VariantFilters:
    coded: dict[str, str] = field(default_factory=dict)  # apiField -> value_code
    numeric_min: dict[str, Decimal] = field(default_factory=dict)  # apiField -> lower bound
    numeric_max: dict[str, Decimal] = field(default_factory=dict)  # apiField -> upper bound
    in_production: bool | None = None  # None → derive from mode
    q: str | None = None  # free-text over the variant / model-group / brand name


@dataclass(frozen=True)
class VariantFacets:
    browse_available: bool
    coded: dict[str, list[tuple[str, int]]]
    numeric: dict[str, tuple[Decimal | None, Decimal | None]]


def _tenant_can_browse(db: Session, *, tenant_id: uuid.UUID) -> bool:
    """PRD: "No provider contract at all → Browse and identification hidden,
    not broken." A tenant with no enabled vehicle-data connection has an
    empty mirror anyway; this makes the emptiness explicit so the screen
    can show a "connect auto-i-dat" state rather than a blank grid.
    Reads persisted entitlement state only — no provider call."""

    return get_catalogue_entitlements(db, tenant_id=tenant_id).has_connection


def list_model_groups(db: Session, *, brand_id: uuid.UUID) -> list[ModelGroup]:
    return list(
        db.scalars(
            select(ModelGroup).where(ModelGroup.brand_id == brand_id).order_by(ModelGroup.name, ModelGroup.id)
        ).all()
    )


def _effective_in_production(filters: VariantFilters, mode: CatalogueBrowseMode) -> bool | None:
    if filters.in_production is not None:
        return filters.in_production
    return True if mode == CatalogueBrowseMode.BUILD else None


def _scope_stmt(*, brand_id: uuid.UUID | None, model_group_id: uuid.UUID | None) -> Select:
    stmt = select(ModelVariant).join(ModelGroup, ModelVariant.model_group_id == ModelGroup.id)
    if model_group_id is not None:
        stmt = stmt.where(ModelVariant.model_group_id == model_group_id)
    elif brand_id is not None:
        stmt = stmt.where(ModelGroup.brand_id == brand_id)
    return stmt


def _apply_filters(stmt: Select, filters: VariantFilters, in_production: bool | None) -> Select:
    for api_field, value_code in filters.coded.items():
        column = CODED_FACET_COLUMNS.get(api_field)
        if column is not None:
            stmt = stmt.where(column == value_code)
    for api_field, lower in filters.numeric_min.items():
        column = NUMERIC_FACET_COLUMNS.get(api_field)
        if column is not None:
            stmt = stmt.where(column >= lower)
    for api_field, upper in filters.numeric_max.items():
        column = NUMERIC_FACET_COLUMNS.get(api_field)
        if column is not None:
            stmt = stmt.where(column <= upper)
    if in_production is True:
        stmt = stmt.where(ModelVariant.model_year_to.is_(None))
    elif in_production is False:
        stmt = stmt.where(ModelVariant.model_year_to.is_not(None))
    if filters.q:
        like = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            ModelVariant.name.ilike(like)
            | ModelGroup.name.ilike(like)
            | ModelGroup.brand.has(Brand.display_name.ilike(like))
        )
    return stmt


def browse_variants(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID | None,
    model_group_id: uuid.UUID | None,
    filters: VariantFilters,
    mode: CatalogueBrowseMode,
    params: SortPageParams,
) -> tuple[list[ModelVariant], str | None, int, bool, bool]:
    """Returns (rows, next_cursor, total, total_is_estimate, browse_available)."""

    if not _tenant_can_browse(db, tenant_id=tenant_id):
        return [], None, 0, False, False

    in_production = _effective_in_production(filters, mode)
    base = _apply_filters(_scope_stmt(brand_id=brand_id, model_group_id=model_group_id), filters, in_production)

    total, total_is_estimate = count_capped(
        db, base, threshold=get_settings().count_exact_threshold
    )

    stmt = base.options(
        # to-one chain — safe to joinedload under LIMIT
        joinedload(ModelVariant.model_group).joinedload(ModelGroup.brand),
        # collection — selectinload so LIMIT stays correct (no cartesian blow-up)
        selectinload(ModelVariant.type_approval_links).joinedload(VariantTypeApproval.type_approval),
    )
    stmt = paginate_query_sorted(stmt, model=ModelVariant, params=params)
    rows = list(db.scalars(stmt).unique().all())
    page_rows, next_cursor = build_sorted_page(rows, params)
    return page_rows, next_cursor, total, total_is_estimate, True


def compute_facets(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    brand_id: uuid.UUID | None,
    model_group_id: uuid.UUID | None,
    mode: CatalogueBrowseMode,
) -> VariantFacets:
    """Facet values are computed over the drill-down *scope* (brand /
    model group / mode) — NOT the user's own predicates, so every real
    alternative stays visible while filtering. Mirror only."""

    if not _tenant_can_browse(db, tenant_id=tenant_id):
        return VariantFacets(browse_available=False, coded={}, numeric={})

    in_production = _effective_in_production(VariantFilters(), mode)
    scope = _scope_stmt(brand_id=brand_id, model_group_id=model_group_id)
    if in_production is True:
        scope = scope.where(ModelVariant.model_year_to.is_(None))

    scope_ids = scope.with_only_columns(ModelVariant.id).subquery()

    coded: dict[str, list[tuple[str, int]]] = {}
    for api_field, column in CODED_FACET_COLUMNS.items():
        rows = db.execute(
            select(column, func.count())
            .where(ModelVariant.id.in_(select(scope_ids.c.id)), column.is_not(None))
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
        ).all()
        coded[api_field] = [(value_code, count) for value_code, count in rows]

    numeric: dict[str, tuple[Decimal | None, Decimal | None]] = {}
    for api_field, column in NUMERIC_FACET_COLUMNS.items():
        low, high = db.execute(
            select(func.min(column), func.max(column)).where(ModelVariant.id.in_(select(scope_ids.c.id)))
        ).one()
        numeric[api_field] = (low, high)

    return VariantFacets(browse_available=True, coded=coded, numeric=numeric)


def type_approval_numbers_for(variant: ModelVariant) -> list[str]:
    """De-duplicated, sorted Typenschein numbers off the already-loaded
    m2m links (browse eager-loads them)."""

    numbers = {
        link.type_approval.type_approval_number
        for link in variant.type_approval_links
        if link.type_approval is not None
    }
    return sorted(numbers)

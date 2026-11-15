"""StockItem endpoints (WP-7 PR-1)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.config import get_settings
from app.core.idempotent_route import IdempotentRoute
from app.core.list_query import GridSchemaRead, grid_schema, parse_list_query
from app.core.permissions import ModuleCode, require_entitlement, require_write
from app.core.sorting import SortField
from app.db import get_db
from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.schemas.purchase import NotionalInputTaxOverrideRequest, PurchaseStornoRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import (
    StockItemConditionChange,
    StockItemConfigurationAttach,
    StockItemCreateRequest,
    StockItemPage,
    StockItemRead,
    StockItemUpdate,
    StockItemVinCorrectionRequest,
)
from app.inventory.services import purchase as purchase_service
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services.stock_grid import DEFAULT_STOCK_ITEM_SORT, STOCK_ITEM_COLUMNS

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(tags=["inventory"], route_class=IdempotentRoute, dependencies=[Depends(require_entitlement(ModuleCode.SALES))])
settings = get_settings()

# KAN-461: every column sorts and filters through the one registry
# (services/stock_grid.py; app.core.list_query). The fixed default order,
# as a hand-built SortField for the callers that page without a request.
_DEFAULT_STOCK_ITEM_SORT = [
    SortField(api_name="updatedAt", column=StockItem.updated_at, direction="desc", nullable=False)
]


def _stock_item_read(item: StockItem) -> StockItemRead:
    """ageingBucket is computed here (PR-7), never stored — every endpoint
    that returns a StockItemRead goes through this one place so it can't
    be forgotten on a new call site.
    """

    base = StockItemRead.model_validate(item, from_attributes=True)
    return base.model_copy(update={"ageing_bucket": stock_item_service.compute_ageing_bucket(item)})


@router.post(
    "/inventory/stock-items",
    response_model=StockItemRead,
    status_code=201,
    description=(
        "Adds a car to this Betrieb's stock pipeline from the configuration the configurator produced. Every "
        "create lands as `lifecycleStatus: pipeline` — a `vin` sent with it is stored on the pipeline item, "
        "never put in stock (ADR-103, ADR-116; KAN-554). Booking the purchase "
        "(`POST /v1/inventory/stock-items/{id}/purchase`) is the only path to `in_stock`; it uses the stored "
        "VIN when the request sends none. Before KAN-554 a create with a `vin` went straight to `in_stock`."
    ),
)
def create_stock_item(
    body: StockItemCreateRequest,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.create_stock_item(db, tenant_id=principal.tenant_id, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.get(
    "/inventory/stock-items/grid-schema",
    response_model=GridSchemaRead,
    description=(
        "KAN-461: the Stock grid's column registry — every field, its type, the conditions `filter` takes on it, "
        "whether it sorts, its label key, whether the grid shows it by default and (an enum) its options. The SPA "
        "derives its columns, sortability and filter fields from it."
    ),
)
def get_stock_item_grid_schema(principal: Principal = Depends(get_current_principal)):
    return grid_schema(STOCK_ITEM_COLUMNS)


def _legacy_filters(
    lifecycle_status: LifecycleStatus | None,
    condition: StockItemCondition | None,
    lifecycle_status_not: LifecycleStatus | None,
    condition_not: StockItemCondition | None,
) -> list[str]:
    """The hand-wired parameters before KAN-461, kept for one release as the
    registry predicates they always meant."""

    legacy = []
    if lifecycle_status is not None:
        legacy.append(f"lifecycleStatus:is:{lifecycle_status.value}")
    if condition is not None:
        legacy.append(f"condition:is:{condition.value}")
    if lifecycle_status_not is not None:
        legacy.append(f"lifecycleStatus:isNot:{lifecycle_status_not.value}")
    if condition_not is not None:
        legacy.append(f"condition:isNot:{condition_not.value}")
    return legacy


@router.get("/inventory/stock-items", response_model=StockItemPage)
def list_stock_items(
    q: str | None = None,
    filter: list[str] | None = Query(
        default=None,
        description=(
            "KAN-461: one predicate per parameter, `field:operator[:value]`, repeatable (ANDed). Fields, operators "
            "and enum values come from `GET /v1/inventory/stock-items/grid-schema`; anything else is 422 "
            "(`details.reason`: unknown_field, operator_not_allowed, filter_malformed, invalid_filter_value). "
            "`in` and `between` take comma-separated values; dates are YYYY-MM-DD, money CHF with up to two "
            "decimals, day counts 0–36500; relative dates resolve in Europe/Zurich."
        ),
    ),
    lifecycle_status: LifecycleStatus | None = Query(
        default=None, deprecated=True, description="Before KAN-461; use `filter=lifecycleStatus:is:<code>`."
    ),
    condition: StockItemCondition | None = Query(
        default=None, deprecated=True, description="Before KAN-461; use `filter=condition:is:<code>`."
    ),
    lifecycle_status_not: LifecycleStatus | None = Query(
        default=None, deprecated=True,
        description="KAN-476's «ist nicht», before KAN-461; use `filter=lifecycleStatus:isNot:<code>`.",
    ),
    condition_not: StockItemCondition | None = Query(
        default=None, deprecated=True,
        description="KAN-476's «ist nicht», before KAN-461; use `filter=condition:isNot:<code>`.",
    ),
    sort: str | None = Query(
        default=None, description="Any grid-schema field that sorts, e.g. 'listPrice:asc,updatedAt:desc'"
    ),
    limit: int = Query(default=settings.pagination_default_limit, ge=1, le=settings.pagination_max_limit),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    query = parse_list_query(
        STOCK_ITEM_COLUMNS,
        filters=[*_legacy_filters(lifecycle_status, condition, lifecycle_status_not, condition_not), *(filter or [])],
        sort=sort,
        default_sort=DEFAULT_STOCK_ITEM_SORT,
        limit=limit,
        cursor=cursor,
    )
    rows, next_cursor, total, total_is_estimate = stock_item_service.list_stock_items(
        db, tenant_id=principal.tenant_id, q=q, query=query
    )
    return StockItemPage(
        items=[_stock_item_read(r) for r in rows],
        next_cursor=next_cursor,
        total=total,
        total_is_estimate=total_is_estimate,
    )


@router.get("/inventory/stock-items/{stock_item_id}", response_model=StockItemRead)
def get_stock_item(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    return _stock_item_read(item)


@router.patch("/inventory/stock-items/{stock_item_id}", response_model=StockItemRead)
def update_stock_item(
    stock_item_id: uuid.UUID,
    body: StockItemUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.update_stock_item(db, item=item, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.post("/inventory/stock-items/{stock_item_id}/configuration", response_model=StockItemRead)
def attach_configuration(
    stock_item_id: uuid.UUID,
    body: StockItemConfigurationAttach,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    """KAN-463 — «Konfiguration zuordnen» for an item without one."""

    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.attach_configuration(
        db, item=item, configuration_id=body.configuration_id, actor_id=principal.user_id
    )
    return _stock_item_read(item)


@router.post("/inventory/stock-items/{stock_item_id}/condition", response_model=StockItemRead)
def change_condition(
    stock_item_id: uuid.UUID,
    body: StockItemConditionChange,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_condition")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.change_condition(db, item=item, condition=body.condition, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.post(
    "/inventory/stock-items/{stock_item_id}/vin-correction",
    response_model=StockItemRead,
    description=(
        "KAN-557 «VIN korrigieren» (ADR-116): corrects the VIN of a car this Betrieb bought — on the stock item, "
        "its vehicle-mdm record and its configuration together, each audited with the reason. Vehicle is asked "
        "first (its own transaction); a refusal there writes nothing here. 409 `vin_already_in_stock`, "
        "`vin_belongs_to_other_vehicle` (another record carries the VIN: one car, merged with «Merge into…»), "
        "`vin_names_merged_vehicle` (only another car's merged duplicate carries it: names the record that car "
        "lives on — the car's own merged duplicate gives the VIN up and takes the old one, KAN-730), "
        "`vin_correction_left_stock`, `vin_correction_needs_vehicle` (a car never bought: correct its "
        "configuration), `vehicle_vin_changed`, `vehicle_merged`; 422 `vin_unchanged`, `configuration_vin_differs`."
    ),
)
def correct_vin(
    stock_item_id: uuid.UUID,
    body: StockItemVinCorrectionRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_vin_correction")),
    db: Session = Depends(get_db),
):
    """On the matrix's own "Stock VIN correction" row: the dealer manager
    (the ticket's «dealer manager, sales manager» — a manager is the Betrieb's
    manager flag, whatever their functional role)."""

    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = stock_item_service.correct_vin(db, item=item, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.post("/inventory/stock-items/{stock_item_id}/purchase", response_model=StockItemRead)
def record_purchase(
    stock_item_id: uuid.UUID,
    body: RecordPurchaseRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_acquisition")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = purchase_service.record_purchase(db, item=item, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.post("/inventory/stock-items/{stock_item_id}/purchase/storno", response_model=StockItemRead)
def storno_purchase(
    stock_item_id: uuid.UUID,
    body: PurchaseStornoRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_acquisition")),
    db: Session = Depends(get_db),
):
    """FR-I-05 «Einkauf stornieren» (KAN-146): on the matrix's "Stock
    acquisition, landed cost, storno" row, like the booking it reverses."""

    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = purchase_service.storno_purchase(db, item=item, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)


@router.patch("/inventory/stock-items/{stock_item_id}/purchase/notional-input-tax", response_model=StockItemRead)
def override_notional_input_tax(
    stock_item_id: uuid.UUID,
    body: NotionalInputTaxOverrideRequest,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("stock_acquisition")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(item.version, if_match, entity_name="StockItem")
    item = purchase_service.override_notional_input_tax(db, item=item, data=body, actor_id=principal.user_id)
    return _stock_item_read(item)

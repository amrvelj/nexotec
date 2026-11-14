"""Marketplace publishing endpoints (WP-7 PR-8, ADR-062)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.concurrency import check_version, require_if_match
from app.core.idempotent_route import IdempotentRoute
from app.core.permissions import ModuleCode, require_entitlement, require_write
from app.db import get_db
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import MarketplaceChannel, StockItemPublishing
from app.inventory.schemas.publishing import (
    AddMediaRequest,
    EquipmentRead,
    ListingTextUpdate,
    MediaRead,
    PublishingRead,
    ReorderMediaRequest,
    UnpublishRequest,
    WebsiteListingRead,
)
from app.inventory.services import publishing as publishing_service
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services import website_listing as website_listing_service
from app.vehicle.public import get_vehicle_equipment

# KAN-198 (D-A-04): the entitlement gate runs before any role check; an
# unlicensed module is 404 for everyone (router-level, so every route).
router = APIRouter(tags=["inventory"], route_class=IdempotentRoute, dependencies=[Depends(require_entitlement(ModuleCode.SALES))])


def _publishing_read(db: Session, item: StockItem, row: StockItemPublishing) -> PublishingRead:
    base = PublishingRead.model_validate(row, from_attributes=True)
    return base.model_copy(update={"blocking_conditions": publishing_service.compute_blocking_conditions(db, item)})


@router.get("/inventory/stock-items/{stock_item_id}/publishing/{channel}", response_model=PublishingRead)
def get_publishing(
    stock_item_id: uuid.UUID,
    channel: MarketplaceChannel,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    row = publishing_service.get_or_create_publishing(db, item, channel)
    db.commit()
    return _publishing_read(db, item, row)


@router.patch("/inventory/stock-items/{stock_item_id}/publishing/{channel}", response_model=PublishingRead)
def update_listing_text(
    stock_item_id: uuid.UUID,
    channel: MarketplaceChannel,
    body: ListingTextUpdate,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    # KAN-396: If-Match names the channel's publishing row (the GET above
    # creates it on first read), the row this PATCH writes and versions.
    row = publishing_service.get_or_create_publishing_for_update(db, item, channel)
    check_version(row.version, if_match, entity_name="StockItemPublishing")
    row = publishing_service.update_listing_text(db, item=item, channel=channel, data=body, actor_id=principal.user_id)
    return _publishing_read(db, item, row)


@router.post("/inventory/stock-items/{stock_item_id}/publishing/{channel}/publish", response_model=PublishingRead)
def publish_to_channel(
    stock_item_id: uuid.UUID,
    channel: MarketplaceChannel,
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    row = publishing_service.publish(db, item=item, channel=channel, actor_id=principal.user_id)
    return _publishing_read(db, item, row)


@router.post("/inventory/stock-items/{stock_item_id}/publishing/{channel}/unpublish", response_model=PublishingRead)
def unpublish_from_channel(
    stock_item_id: uuid.UUID,
    channel: MarketplaceChannel,
    body: UnpublishRequest,
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    row = publishing_service.unpublish(db, item=item, channel=channel, confirm=body.confirm, actor_id=principal.user_id)
    return _publishing_read(db, item, row)


@router.get("/inventory/stock-items/{stock_item_id}/equipment", response_model=EquipmentRead)
def get_equipment(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """§ ADR-062 — equipment is a fact about the car, owned and edited in
    app.vehicle; the publishing tab only reads it, via the same
    cross-context public surface promotion (PR-2) already established.
    Empty for a pipeline item with no vehicle_id yet.
    """

    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    if item.vehicle_id is None:
        return EquipmentRead(ausstattung_codes=[], extras=[], eigenschaften=[], provider_ausstattung={})
    return EquipmentRead.model_validate(get_vehicle_equipment(db, item.vehicle_id))


@router.get("/inventory/stock-items/{stock_item_id}/media", response_model=list[MediaRead])
def list_media(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    media = publishing_service.list_media(db, tenant_id=principal.tenant_id, stock_item_id=stock_item_id)
    return [MediaRead.model_validate(m, from_attributes=True) for m in media]


@router.post("/inventory/stock-items/{stock_item_id}/media", response_model=MediaRead, status_code=201)
def add_media(
    stock_item_id: uuid.UUID,
    body: AddMediaRequest,
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    media = publishing_service.add_media(db, item=item, url=body.url)
    return MediaRead.model_validate(media, from_attributes=True)


@router.post("/inventory/stock-items/{stock_item_id}/media/reorder", response_model=list[MediaRead])
def reorder_media(
    stock_item_id: uuid.UUID,
    body: ReorderMediaRequest,
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    media = publishing_service.reorder_media(db, item=item, ordered_media_ids=body.ordered_media_ids)
    return [MediaRead.model_validate(m, from_attributes=True) for m in media]


@router.delete("/inventory/stock-items/{stock_item_id}/media/{media_id}", status_code=204)
def remove_media(
    stock_item_id: uuid.UUID,
    media_id: uuid.UUID,
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    publishing_service.remove_media(db, item=item, media_id=media_id)


# --- The Betrieb's own website (KAN-225, D-A-26) ------------------------------------------------


def _website_read(db: Session, item: StockItem) -> WebsiteListingRead:
    row = website_listing_service.read_listing(db, item)
    return WebsiteListingRead(
        stock_item_id=item.id,
        is_listed=row.is_listed,
        listed_at=row.listed_at,
        unlisted_at=row.unlisted_at,
        blocking_conditions=website_listing_service.blocking_conditions(db, item),
        version=row.version,
    )


@router.get("/inventory/stock-items/{stock_item_id}/website-listing", response_model=WebsiteListingRead)
def get_website_listing(
    stock_item_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Whether the car is on the Betrieb's website, and what would refuse
    it there — the publishing profile's missing items included (FR-A-05)."""

    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    return _website_read(db, item)


@router.post("/inventory/stock-items/{stock_item_id}/website-listing/publish", response_model=WebsiteListingRead)
def publish_to_website(
    stock_item_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(website_listing_service.read_listing(db, item).version, if_match, entity_name="WebsiteListing")
    website_listing_service.list_on_website(db, item=item, actor_id=principal.user_id)
    return _website_read(db, item)


@router.post("/inventory/stock-items/{stock_item_id}/website-listing/unpublish", response_model=WebsiteListingRead)
def unpublish_from_website(
    stock_item_id: uuid.UUID,
    if_match: int = Depends(require_if_match),
    principal: Principal = Depends(require_write("marketplace_publishing")),
    db: Session = Depends(get_db),
):
    """Takes the car off the website. Nothing is deleted at a third party, so
    no confirmation is asked (unlike a marketplace's full delivery)."""

    item = stock_item_service.get_stock_item_for_update_or_404(db, principal.tenant_id, stock_item_id)
    check_version(website_listing_service.read_listing(db, item).version, if_match, entity_name="WebsiteListing")
    website_listing_service.unlist_from_website(db, item=item, actor_id=principal.user_id)
    return _website_read(db, item)

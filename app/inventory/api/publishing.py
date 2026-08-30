"""Marketplace publishing endpoints (WP-7 PR-8, ADR-062)."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import Principal, get_current_principal
from app.core.permissions import require_write
from app.db import get_db
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import MarketplaceChannel, StockItemPublishing
from app.inventory.schemas.publishing import (
    AddMediaRequest,
    ListingTextUpdate,
    MediaRead,
    PublishingRead,
    ReorderMediaRequest,
    UnpublishRequest,
)
from app.inventory.services import publishing as publishing_service
from app.inventory.services import stock_item as stock_item_service
from app.vehicle.public import get_vehicle_equipment

router = APIRouter(tags=["inventory"])


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
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    row = publishing_service.update_listing_text(db, item=item, channel=channel, data=body, actor_id=principal.user_id)
    return _publishing_read(db, item, row)


@router.post("/inventory/stock-items/{stock_item_id}/publishing/{channel}/publish", response_model=PublishingRead)
def publish_to_channel(
    stock_item_id: uuid.UUID,
    channel: MarketplaceChannel,
    principal: Principal = Depends(require_write("stock_items")),
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
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    row = publishing_service.unpublish(db, item=item, channel=channel, confirm=body.confirm, actor_id=principal.user_id)
    return _publishing_read(db, item, row)


@router.get("/inventory/stock-items/{stock_item_id}/equipment")
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
        return {"ausstattungCodes": [], "extras": [], "eigenschaften": [], "providerAusstattung": {}}
    return get_vehicle_equipment(db, item.vehicle_id)


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
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    media = publishing_service.add_media(db, item=item, url=body.url)
    return MediaRead.model_validate(media, from_attributes=True)


@router.post("/inventory/stock-items/{stock_item_id}/media/reorder", response_model=list[MediaRead])
def reorder_media(
    stock_item_id: uuid.UUID,
    body: ReorderMediaRequest,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    media = publishing_service.reorder_media(db, item=item, ordered_media_ids=body.ordered_media_ids)
    return [MediaRead.model_validate(m, from_attributes=True) for m in media]


@router.delete("/inventory/stock-items/{stock_item_id}/media/{media_id}", status_code=204)
def remove_media(
    stock_item_id: uuid.UUID,
    media_id: uuid.UUID,
    principal: Principal = Depends(require_write("stock_items")),
    db: Session = Depends(get_db),
):
    item = stock_item_service.get_stock_item_or_404(db, principal.tenant_id, stock_item_id)
    publishing_service.remove_media(db, item=item, media_id=media_id)

"""WP-7 PR-8: marketplace publishing (ADR-062)."""

import uuid
from decimal import Decimal

import pytest

from app.core.errors import BadRequestError, ConflictError
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.models.stock_item_publishing import MarketplaceChannel, PublishingState, StockItemMedia
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.publishing import (
    add_media,
    compute_blocking_conditions,
    list_media,
    publish,
    remove_media,
    reorder_media,
    unpublish,
)
from app.inventory.services.stock_item import create_stock_item, update_stock_item


def _make_ready_item(db_session, tenant_id):
    """A promoted (in_stock), priced item — one blocking condition away
    (no media) from being publishable."""

    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    item = promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")
    return update_stock_item(db_session, item=item, data=StockItemUpdate(effective_price=Decimal("19900.00")), actor_id=uuid.uuid4())


def test_pipeline_item_is_blocked_with_the_marketplace_field_name(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Factory order", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    conditions = compute_blocking_conditions(db_session, item)
    fields = {c.field for c in conditions}
    assert "Lagerstatus" in fields
    lagerstatus = next(c for c in conditions if c.field == "Lagerstatus")
    assert lagerstatus.message == "Eine Werksbestellung steht noch nicht am Lager."


def test_no_photos_and_no_price_are_both_blocking(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    item = promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")
    conditions = compute_blocking_conditions(db_session, item)
    fields = {c.field for c in conditions}
    assert "Bilder" in fields
    assert "Preis" in fields


def test_publish_refused_while_blocking_conditions_remain(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Factory order", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    with pytest.raises(ConflictError):
        publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())


def test_publish_succeeds_once_every_condition_clears(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    add_media(db_session, item=item, url="https://example.com/photo1.jpg")

    row = publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())
    assert row.state == PublishingState.PUBLISHED
    assert row.last_published_at is not None


def test_unpublish_requires_explicit_confirm(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    add_media(db_session, item=item, url="https://example.com/photo1.jpg")
    publish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, actor_id=uuid.uuid4())

    with pytest.raises(BadRequestError):
        unpublish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, confirm=False, actor_id=uuid.uuid4())

    row = unpublish(db_session, item=item, channel=MarketplaceChannel.AUTOSCOUT24, confirm=True, actor_id=uuid.uuid4())
    assert row.state == PublishingState.NOT_PUBLISHED


def test_media_position_1_is_the_main_image_no_is_main_column_exists(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    first = add_media(db_session, item=item, url="https://example.com/1.jpg")
    add_media(db_session, item=item, url="https://example.com/2.jpg")

    assert first.position == 1
    assert not hasattr(StockItemMedia, "is_main")


def test_reorder_media_changes_the_main_image(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    first = add_media(db_session, item=item, url="https://example.com/1.jpg")
    second = add_media(db_session, item=item, url="https://example.com/2.jpg")

    reordered = reorder_media(db_session, item=item, ordered_media_ids=[second.id, first.id])
    assert reordered[0].id == second.id
    assert reordered[0].position == 1
    assert reordered[1].id == first.id
    assert reordered[1].position == 2


def test_max_sixteen_images_enforced(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    for i in range(16):
        add_media(db_session, item=item, url=f"https://example.com/{i}.jpg")

    with pytest.raises(ConflictError):
        add_media(db_session, item=item, url="https://example.com/one-too-many.jpg")


def test_remove_media_closes_the_position_gap(db_session):
    tenant_id = uuid.uuid4()
    item = _make_ready_item(db_session, tenant_id)
    first = add_media(db_session, item=item, url="https://example.com/1.jpg")
    second = add_media(db_session, item=item, url="https://example.com/2.jpg")
    third = add_media(db_session, item=item, url="https://example.com/3.jpg")

    remove_media(db_session, item=item, media_id=second.id)

    remaining = list_media(db_session, tenant_id=tenant_id, stock_item_id=item.id)
    assert [m.id for m in remaining] == [first.id, third.id]
    assert [m.position for m in remaining] == [1, 2]

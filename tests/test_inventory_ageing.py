"""WP-7 PR-7: ageing — two independent mechanisms (FR-I-14).

ageingBucket (fixed 0-60/61-120/121+, grid colour cue) is never the same
field or mechanism as Dealership.ageing_alert_thresholds (genuinely
dealer-configurable, notification-only)."""

import datetime as dt
import uuid

from app.core.base import utcnow
from app.inventory.models.stock_item import AgeingBucket, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate, StockItemRead
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.stock_item import compute_ageing_bucket, create_stock_item
from app.platform.schemas.dealership import DealershipRead


def test_ageing_bucket_is_none_while_pipeline(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    assert compute_ageing_bucket(item) is None


def test_ageing_bucket_boundaries(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    promoted = promote_to_vehicle_mdm(db_session, item=item, vin="1HGCM82633A004352")

    promoted.in_stock_at = utcnow() - dt.timedelta(days=10)
    assert compute_ageing_bucket(promoted) == AgeingBucket.GREEN

    promoted.in_stock_at = utcnow() - dt.timedelta(days=90)
    assert compute_ageing_bucket(promoted) == AgeingBucket.AMBER

    promoted.in_stock_at = utcnow() - dt.timedelta(days=150)
    assert compute_ageing_bucket(promoted) == AgeingBucket.RED


def test_ageing_alert_thresholds_is_a_separate_dealership_field_never_stock_item():
    """Fixed grid bucket vs. configurable alert threshold are two
    genuinely different mechanisms — pinned here so they can't quietly
    merge into one field later."""

    assert "ageing_alert_thresholds" not in StockItemRead.model_fields
    assert "ageing_alert_thresholds" in DealershipRead.model_fields
    assert "ageing_bucket" in StockItemRead.model_fields

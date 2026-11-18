"""WP-7 PR-1: StockItem core + the two-axis lifecycle (ADR-054)."""

import uuid

from sqlalchemy import select

from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate, StockItemUpdate
from app.inventory.services.stock_item import (
    allocate_stock_number,
    change_condition,
    create_stock_item,
    update_stock_item,
)


def test_lifecycle_status_has_exactly_three_values():
    """PRD-Stock's own data-spec table (revised 2026-08-16, ADR-054) is
    authoritative over a stale four-value draft that included "sold" — a
    sold vehicle is never a lifecycle value, it is absent from the active
    list (FR-I-12, enforced by left_stock_at in PR-5). Pinned here so a
    future migration can't silently reintroduce a 4th member.
    """

    assert {s.value for s in LifecycleStatus} == {"pipeline", "in_stock", "storno_pending"}


def test_reservation_state_is_independent_of_lifecycle_status():
    assert {s.value for s in ReservationState} == {"none", "reserved"}


def test_create_stock_item_without_vin_starts_pipeline(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Volkswagen Käfer 1303 LS Cabriolet", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    assert item.lifecycle_status == LifecycleStatus.PIPELINE
    assert item.reservation_state == ReservationState.NONE
    assert item.stock_number.startswith("S-")
    assert item.vehicle_id is None


def test_create_stock_item_with_vin_lands_in_pipeline_carrying_it(db_session):
    """KAN-554 (ADR-116; Anto, 2026-11-05, option A): a VIN on create never
    puts the car in stock — purchase booking does (ADR-103). The VIN is
    stored; the vehicle is not resolved, and a client's vehicleId is not kept."""

    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(
            vehicle_label="Volkswagen Käfer 1303 LS Cabriolet",
            condition=StockItemCondition.USED,
            vehicle_id=uuid.uuid4(),
            vin="WVWUWDJ62012T0KD3",
        ),
        actor_id=uuid.uuid4(),
    )
    assert item.lifecycle_status == LifecycleStatus.PIPELINE
    assert item.in_stock_at is None
    assert item.vin == "WVWUWDJ62012T0KD3"
    assert item.vehicle_id is None


def test_stock_number_allocation_is_per_tenant_and_gapless_on_reuse(db_session):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    assert allocate_stock_number(db_session, tenant_a) == "S-000001"
    assert allocate_stock_number(db_session, tenant_a) == "S-000002"
    # A different tenant gets its own counter, starting at 1 again — a
    # stock number is dealership-owned stock, not a global fact.
    assert allocate_stock_number(db_session, tenant_b) == "S-000001"


def test_create_stock_item_publishes_added_event(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    message = db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.added", OutboxMessage.aggregate_id == item.id
        )
    )
    assert message is not None
    assert message.tenant_id == tenant_id


def test_change_condition_publishes_condition_changed_event_only_on_real_change(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    change_condition(db_session, item=item, condition=StockItemCondition.DEMO, actor_id=uuid.uuid4())
    messages = list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == "inventory.stock_item.condition_changed")
        )
    )
    assert len(messages) == 1

    # Same condition again — no new event, no spurious version bump beyond
    # the one this call itself makes.
    change_condition(db_session, item=item, condition=StockItemCondition.DEMO, actor_id=uuid.uuid4())
    messages = list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.event_type == "inventory.stock_item.condition_changed")
        )
    )
    assert len(messages) == 1


def _condition_events(db_session) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == "inventory.stock_item.condition_changed")
            .order_by(OutboxMessage.id)
        )
    )


def test_a_condition_changed_by_patch_publishes_condition_changed_once(client, db_session):
    """KAN-432 (rule 4): `PATCH /v1/inventory/stock-items/{id}` accepts
    `condition`; it publishes the same event as the condition endpoint, in
    the same transaction. Before, the PATCH wrote it with a bare setattr."""

    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False,
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.patch(
        f"/v1/inventory/stock-items/{item.id}",
        json={"condition": "demo", "vehicleLabel": "Škoda Octavia RS"},
        headers={**headers, "If-Match": str(item.version)},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["condition"] == "demo"
    assert resp.json()["version"] == item.version + 1
    events = _condition_events(db_session)
    assert len(events) == 1
    assert events[0].tenant_id == tenant_id
    assert events[0].aggregate_id == item.id
    assert events[0].payload["previousCondition"] == "used"
    assert events[0].payload["condition"] == "demo"

    # The same condition again, or a PATCH without one, is no change.
    for body in ({"condition": "demo"}, {"vehicleLabel": "Škoda Octavia Combi"}):
        version = client.get(f"/v1/inventory/stock-items/{item.id}", headers=headers).json()["version"]
        resp = client.patch(
            f"/v1/inventory/stock-items/{item.id}", json=body, headers={**headers, "If-Match": str(version)}
        )
        assert resp.status_code == 200, resp.text
    assert len(_condition_events(db_session)) == 1


def test_a_condition_change_by_either_route_publishes_exactly_one_event_each(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )

    update_stock_item(db_session, item=item, data=StockItemUpdate(condition=StockItemCondition.DEMO), actor_id=None)
    assert len(_condition_events(db_session)) == 1
    change_condition(db_session, item=item, condition=StockItemCondition.USED, actor_id=None)

    events = _condition_events(db_session)
    assert [(e.payload["previousCondition"], e.payload["condition"]) for e in events] == [
        ("used", "demo"),
        ("demo", "used"),
    ]


def test_vin_is_unique_per_tenant_only_when_set(db_session):
    tenant_id = uuid.uuid4()
    vehicle_id = uuid.uuid4()
    create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(
            vehicle_label="Car one", condition=StockItemCondition.USED, vehicle_id=vehicle_id, vin="1HGCM82633A004352"
        ),
        actor_id=uuid.uuid4(),
    )
    # Two pipeline items with NO vin at all must not collide against each
    # other — the partial unique index only applies WHERE vin IS NOT NULL.
    create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Pipeline car A", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Pipeline car B", condition=StockItemCondition.NEW),
        actor_id=uuid.uuid4(),
    )
    rows = list(db_session.scalars(select(StockItem).where(StockItem.tenant_id == tenant_id)).all())
    assert len(rows) == 3  # all three created without a spurious unique-violation on the two null VINs


def test_update_stock_item_bumps_version(db_session):
    tenant_id = uuid.uuid4()
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    original_version = item.version
    updated = update_stock_item(
        db_session, item=item, data=StockItemUpdate(odometer_km=12345), actor_id=uuid.uuid4()
    )
    assert updated.odometer_km == 12345
    assert updated.version == original_version + 1

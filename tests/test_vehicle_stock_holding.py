"""KAN-667 (Anto, 2026-11-13, option A; ADR-131, rule 7, rule 10): a car the
caller's Betrieb holds in stock keeps its VIN on the Vehicle 360 — the VIN is
corrected on the stock car with «VIN korrigieren» (KAN-557), which rewrites
the stock item, the record and the configuration together.

Vehicle may not ask Stock (ADR-131), so it keeps its own projection of the
records each Betrieb holds in stock (`vehicle_stock_holding`), fed by Stock's
facts `inventory.pipeline_vehicle.vin_assigned` and `inventory.stock_item.sold`
in any order, plus the facts it has not applied yet; filled for older cars by
the backfill script, and compared with Stock every night. Only the caller's
own Betrieb counts: another Betrieb's stock never refuses the change, nor
shows in a refusal or the detail read. On Postgres, over HTTP where the route
is under test.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import InProcessTransport
from app.core.reconciliation import ReconciliationAlarm
from app.db import SessionLocal
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.purchase import PurchaseStornoRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.purchase import record_purchase, storno_purchase
from app.inventory.services.stock_item import create_stock_item
from app.vehicle import reconciliation as vehicle_reconciliation
from app.vehicle.consumers import handle_stock_holding_message
from app.vehicle.models.stock_holding import VehicleStockHolding
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import STOCK_HOLDING_CONSUMERS
from app.vehicle.services.stock_holding import (
    SOLD,
    VIN_ASSIGNED,
    apply_stock_holding_message,
    held_stock_item,
)
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm
from app.worker import register_handlers
from scripts import backfill_vehicle_stock_holding as backfill_script
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.test_sales_manual_configuration_link import _session_factory
from tests.token_support import dealer_group_of

VIN = "WVWZZZ1KZAW667001"
OTHER_VIN = "WVWZZZ1KZAW667002"
NEW_VIN = "WVWZZZ1KZAW667009"
REFUSED = "vin_change_needs_vin_correction"


# --- helpers ---------------------------------------------------------------------


def _bought(db_session, tenant_id: uuid.UUID, vin: str = VIN) -> StockItem:
    """A car bought into `tenant_id`'s stock through the production path:
    the booking promotes it (`vin_assigned`) and books the purchase."""

    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED, vin=vin),
        actor_id=uuid.uuid4(),
    )
    item = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster", supplier_is_vat_registered=False,
            purchase_price=Decimal("20000.00"), purchase_date=dt.date(2026, 11, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    db_session.commit()
    assert item.vehicle_id is not None
    return item


def _message(db_session, event_type: str, item_id: uuid.UUID) -> OutboxMessage:
    db_session.expire_all()
    return db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == event_type, OutboxMessage.aggregate_id == item_id)
    ).one()


def _deliver(engine, message: OutboxMessage) -> None:
    """Through the transport and the consumer harness, as the worker does."""

    transport = InProcessTransport(_session_factory(engine))
    for event_type, consumer_name in STOCK_HOLDING_CONSUMERS.items():
        transport.register(event_type, consumer_name=consumer_name, handler=handle_stock_holding_message)
    transport.deliver(message)


def _mark_published(db_session, message: OutboxMessage) -> None:
    """The worker publishes a message once every consumer has processed it."""

    db_session.execute(
        update(OutboxMessage).where(OutboxMessage.id == message.id).values(status=OutboxStatus.PUBLISHED, published_at=utcnow())
    )
    db_session.commit()


def _fed(db_session, engine, item: StockItem) -> None:
    message = _message(db_session, VIN_ASSIGNED, item.id)
    _deliver(engine, message)
    _mark_published(db_session, message)


def _rows(db_session, item_id: uuid.UUID) -> list[VehicleStockHolding]:
    db_session.expire_all()
    return list(db_session.scalars(select(VehicleStockHolding).where(VehicleStockHolding.stock_item_id == item_id)))


def _token(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
    )


def _get(client, token: str, vehicle_id: uuid.UUID):
    return client.get(f"/v1/vehicle-mdm/{vehicle_id}", headers={"Authorization": f"Bearer {token}"})


def _patch(client, token: str, vehicle_id: uuid.UUID, body: dict):
    version = _get(client, token, vehicle_id).json()["version"]
    return client.patch(
        f"/v1/vehicle-mdm/{vehicle_id}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "If-Match": str(version)},
    )


def _record_vin(db_session, vehicle_id: uuid.UUID) -> str:
    db_session.expire_all()
    return db_session.get(VehicleMdm, vehicle_id).vin


# --- the consumers: Stock's facts feed the projection ------------------------------


def test_the_worker_registers_one_consumer_per_stock_holding_fact():
    transport = InProcessTransport(SessionLocal)
    register_handlers(transport)

    assert set(STOCK_HOLDING_CONSUMERS.values()) <= transport.registered_consumer_names()
    assert STOCK_HOLDING_CONSUMERS == {
        "inventory.pipeline_vehicle.vin_assigned": "vehicle.stock_holding_on_vin_assigned",
        "inventory.stock_item.sold": "vehicle.stock_holding_on_sold",
    }


def test_the_vin_assigned_fact_carries_the_stock_number_and_puts_the_record_in_the_projection(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    message = _message(db_session, VIN_ASSIGNED, item.id)
    assert message.payload["stockNumber"] == item.stock_number  # additive since KAN-667

    _deliver(engine, message)

    [row] = _rows(db_session, item.id)
    assert (row.tenant_id, row.vehicle_id, row.stock_number, row.left_stock_at) == (
        dealership.id, item.vehicle_id, item.stock_number, None,
    )
    held = held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=item.vehicle_id)
    assert (held.stock_item_id, held.stock_number) == (item.id, item.stock_number)


def test_the_sold_fact_ends_the_hold(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)

    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-667")
    _deliver(engine, _message(db_session, SOLD, item.id))

    [row] = _rows(db_session, item.id)
    assert row.left_stock_at is not None and row.vehicle_id == item.vehicle_id
    assert held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=item.vehicle_id) is None


def test_the_facts_build_the_same_row_whatever_order_they_arrive_in(db_session, engine):
    """Unordered delivery: the sale first, then the record — the item has
    still left stock, and the record is still named."""

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-667")

    _deliver(engine, _message(db_session, SOLD, item.id))
    [first] = _rows(db_session, item.id)
    assert (first.vehicle_id, first.stock_number) == (None, None) and first.left_stock_at is not None
    _deliver(engine, _message(db_session, VIN_ASSIGNED, item.id))

    [row] = _rows(db_session, item.id)
    assert (row.vehicle_id, row.stock_number) == (item.vehicle_id, item.stock_number)
    assert row.left_stock_at == first.left_stock_at
    assert held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=item.vehicle_id) is None


def test_a_fact_applied_twice_changes_nothing_even_past_the_harness(db_session, engine):
    """The harness stops a redelivery by eventId (and the redelivery test
    proves it for both consumers); the merge itself is idempotent too."""

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    message = _message(db_session, VIN_ASSIGNED, item.id)

    apply_stock_holding_message(db_session, message)
    db_session.commit()
    [before] = _rows(db_session, item.id)
    apply_stock_holding_message(db_session, message)
    db_session.commit()

    [after] = _rows(db_session, item.id)
    assert (after.id, after.updated_at, after.vehicle_id) == (before.id, before.updated_at, before.vehicle_id)


def test_a_stornoed_car_still_holds_its_record(db_session, engine):
    """«Einkauf stornieren» returns the car to pipeline with its VIN and
    record (KAN-146): it still binds them, and «VIN korrigieren» still
    corrects it — so the projection keeps it."""

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    db_session.refresh(item)
    storno_purchase(db_session, item=item, data=PurchaseStornoRequest(reason="Lieferung storniert"), actor_id=None)
    db_session.commit()

    held = held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=item.vehicle_id)
    assert held is not None and held.stock_item_id == item.id


# --- the identity PATCH ------------------------------------------------------------


def test_a_vin_change_on_a_car_in_the_callers_stock_is_refused_with_its_stock_car(client, db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    audits_before = db_session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.vehicle_id))

    response = _patch(client, _token(dealership.id), item.vehicle_id, {"vin": NEW_VIN})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {
        "reason": REFUSED,
        "field": "vin",
        "stockItemId": str(item.id),
        "stockNumber": item.stock_number,
    }
    assert _record_vin(db_session, item.vehicle_id) == VIN
    assert db_session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == item.vehicle_id)) == audits_before
    summary = db_session.scalars(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "vehicle.vehicle_mdm.summary_changed", OutboxMessage.aggregate_id == item.vehicle_id
        )
    ).all()
    assert summary == []


def test_a_patch_that_leaves_the_vin_alone_is_not_refused(client, db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    token = _token(dealership.id)

    other_field = _patch(client, token, item.vehicle_id, {"stammnummer": "123.456.7"})
    same_vin = _patch(client, token, item.vehicle_id, {"vin": VIN, "typeApprovalNumber": "1VA123"})

    assert other_field.status_code == 200, other_field.text
    assert other_field.json()["stammnummer"] == "123.456.7"
    assert same_vin.status_code == 200, same_vin.text
    assert same_vin.json()["stockItem"] == {"id": str(item.id), "stockNumber": item.stock_number}


@pytest.mark.parametrize("status", [OutboxStatus.PENDING, OutboxStatus.DEAD])
def test_a_fact_not_yet_applied_already_refuses_the_change(client, db_session, status):
    """Fail safe against the outbox's delay: Stock committed the record's
    assignment, Vehicle's consumer has not applied it (pending, or
    dead-lettered until it is replayed) — the VIN is refused all the same."""

    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    message = _message(db_session, VIN_ASSIGNED, item.id)
    db_session.execute(update(OutboxMessage).where(OutboxMessage.id == message.id).values(status=status))
    db_session.commit()
    assert _rows(db_session, item.id) == []

    response = _patch(client, _token(dealership.id), item.vehicle_id, {"vin": NEW_VIN})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["stockItemId"] == str(item.id)
    assert response.json()["error"]["details"]["stockNumber"] == item.stock_number


def test_a_car_that_left_stock_may_be_changed_again(client, db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-667")
    # The sale is applied; the record's own assignment is still pending: an
    # item the projection knows has left stock never counts.
    _deliver(engine, _message(db_session, SOLD, item.id))

    response = _patch(client, _token(dealership.id), item.vehicle_id, {"vin": NEW_VIN})

    assert response.status_code == 200, response.text
    assert response.json()["stockItem"] is None


def test_another_betriebs_stock_never_refuses_nor_shows(client, db_session, engine):
    """Rule 7: the car is in Betrieb A's stock. Betrieb B's PATCH is not
    refused and B's read names no stock car — either would confirm that
    another Betrieb holds it. (A's stock item then differs from the record;
    inventory's nightly check reports it, as for «VIN korrigieren», KAN-557.)"""

    holder = _make_dealership(db_session)
    other = _make_dealership(db_session)
    item = _bought(db_session, holder.id)
    _fed(db_session, engine, item)

    assert _get(client, _token(holder.id), item.vehicle_id).json()["stockItem"] == {
        "id": str(item.id), "stockNumber": item.stock_number,
    }
    other_read = _get(client, _token(other.id), item.vehicle_id).json()
    assert other_read["stockItem"] is None

    response = _patch(client, _token(other.id), item.vehicle_id, {"vin": NEW_VIN})

    assert response.status_code == 200, response.text
    assert response.json()["stockItem"] is None
    assert str(item.id) not in response.text
    assert _record_vin(db_session, item.vehicle_id) == NEW_VIN


def test_another_betriebs_fact_in_flight_never_refuses(client, db_session):
    holder = _make_dealership(db_session)
    other = _make_dealership(db_session)
    item = _bought(db_session, holder.id)  # its vin_assigned is pending: not applied yet

    response = _patch(client, _token(other.id), item.vehicle_id, {"vin": NEW_VIN})

    assert response.status_code == 200, response.text
    assert response.json()["stockItem"] is None


def test_the_detail_read_names_no_stock_car_for_a_record_no_betrieb_holds(client, db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id, vin=OTHER_VIN)
    _fed(db_session, engine, item)
    free = create_vehicle_mdm(db_session, vin=VIN, catalogue_variant_id=None)

    read = _get(client, _token(dealership.id), free.id)

    assert read.status_code == 200, read.text
    assert read.json()["stockItem"] is None


# --- the backfill --------------------------------------------------------------


@pytest.fixture
def factory(engine):
    return _session_factory(engine)


def _published_before_the_consumers(db_session, item: StockItem) -> None:
    """A car bought before KAN-667: its `vin_assigned` was published to the
    consumers of the day, none of them Vehicle's."""

    _mark_published(db_session, _message(db_session, VIN_ASSIGNED, item.id))


def test_the_backfill_dry_run_writes_nothing_and_refuses_a_commit(db_session, engine, factory, monkeypatch):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _published_before_the_consumers(db_session, item)

    report = backfill_script.backfill(commit=False, session_factory=factory)

    assert (report.items_read, report.rows_written) == (1, 1)
    assert _rows(db_session, item.id) == []

    # KAN-263: the dry run runs the real merge and refuses any commit on the way.
    from app.vehicle.services import stock_holding

    real = stock_holding.apply_stock_holding_fact

    def committing(db, fact, *, observed_at):
        changed = real(db, fact, observed_at=observed_at)
        db.commit()
        return changed

    monkeypatch.setattr(stock_holding, "apply_stock_holding_fact", committing)
    with pytest.raises(RuntimeError, match="dry run"):
        backfill_script.backfill(commit=False, session_factory=factory)
    assert _rows(db_session, item.id) == []


def test_the_backfill_fills_the_projection_and_never_undoes_a_newer_fact(client, db_session, engine, factory):
    dealership = _make_dealership(db_session)
    held = _bought(db_session, dealership.id)
    _published_before_the_consumers(db_session, held)
    sold = _bought(db_session, dealership.id, vin=OTHER_VIN)
    _published_before_the_consumers(db_session, sold)
    never_bought = create_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="VW Polo", condition=StockItemCondition.USED, vin="WVWZZZ1KZAW667003"),
        actor_id=None,
    )
    db_session.commit()
    # Before the backfill, a car bought before the consumers is not protected.
    assert held_stock_item(db_session, tenant_id=dealership.id, vehicle_id=held.vehicle_id) is None

    report = backfill_script.backfill(commit=True, session_factory=factory)

    assert report.items_read == 2  # the pipeline car has no record yet
    [row] = _rows(db_session, held.id)
    assert (row.vehicle_id, row.stock_number, row.left_stock_at) == (held.vehicle_id, held.stock_number, None)
    assert _rows(db_session, never_bought.id) == []
    response = _patch(client, _token(dealership.id), held.vehicle_id, {"vin": NEW_VIN})
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == REFUSED

    # A sale consumed after the read: a second run's older read never undoes it.
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=sold.id, invoice_ref="INV-667")
    _deliver(engine, _message(db_session, SOLD, sold.id))
    from app.vehicle.public import apply_backfilled_holding

    assert apply_backfilled_holding(
        db_session, tenant_id=dealership.id, stock_item_id=sold.id, stock_number=sold.stock_number,
        vehicle_id=sold.vehicle_id, read_at=utcnow() - dt.timedelta(minutes=5),
    ) is False
    db_session.commit()
    assert _rows(db_session, sold.id)[0].left_stock_at is not None

    again = backfill_script.backfill(commit=True, session_factory=factory)
    assert (again.items_read, again.rows_written) == (1, 0)


# --- reconciliation (rule 10) --------------------------------------------------------

_DIFFERS = "vehicle_stock_holding differs from its stock_item"
_MISSING = "stock item holding a record missing from vehicle_stock_holding"
_STOCK_VIN_DIFFERS = "stock_item.vin differs from its vehicle_mdm.vin"


def _findings(db_session, module=vehicle_reconciliation) -> dict[str, set]:
    try:
        module.run(db_session)
    except ReconciliationAlarm as alarm:
        found: dict[str, set] = {}
        for orphan in alarm.orphans:
            found.setdefault(orphan.check_label, set()).add(orphan.source_row_id)
        return found
    return {}


def _age(db_session, item: StockItem, hours: int = 2) -> None:
    past = utcnow() - dt.timedelta(hours=hours)
    db_session.execute(update(StockItem).where(StockItem.id == item.id).values(updated_at=past))
    db_session.commit()


def test_a_car_in_stock_the_projection_does_not_hold_is_reported_after_the_grace(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _published_before_the_consumers(db_session, item)  # missed: no row

    assert _MISSING not in _findings(db_session), "a car changed within the hour may have its fact in flight"

    _age(db_session, item)

    assert _findings(db_session).get(_MISSING) == {item.id}


def test_a_missed_sale_is_reported(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-667")
    _age(db_session, item)  # the sold fact was lost (dead-lettered, never replayed)

    assert _findings(db_session).get(_DIFFERS) == {_rows(db_session, item.id)[0].id}


def test_a_fed_projection_reports_nothing(db_session, engine):
    dealership = _make_dealership(db_session)
    item = _bought(db_session, dealership.id)
    _fed(db_session, engine, item)
    _age(db_session, item)

    findings = _findings(db_session)
    assert _DIFFERS not in findings and _MISSING not in findings
    assert "vehicle_stock_holding.stock_item_id -> stock_item.id" not in findings
    assert "vehicle_stock_holding.tenant_id -> dealership.id" not in findings


def test_the_stock_vin_check_stays_the_backstop(client, db_session, engine):
    """The PATCH refusal does not replace KAN-557's nightly check: a car whose
    record another Betrieb changed is still reported by inventory."""

    from app.inventory import reconciliation as inventory_reconciliation

    holder = _make_dealership(db_session)
    other = _make_dealership(db_session)
    item = _bought(db_session, holder.id)
    _fed(db_session, engine, item)
    assert _patch(client, _token(other.id), item.vehicle_id, {"vin": NEW_VIN}).status_code == 200

    assert _findings(db_session, inventory_reconciliation).get(_STOCK_VIN_DIFFERS) == {item.id}

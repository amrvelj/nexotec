"""WP-7 PR-5: the invoicing gate (ADR-052)."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.pagination import SortPageParams
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.purchase import RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.purchase import record_purchase
from app.inventory.services.stock_item import create_stock_item, list_stock_items
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType


def _make_dealership(db_session) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        dealer_group_id=group.id,
        legal_name="Garage AG",
        dealer_license_number="ZH-1",
        license_state="ZH",
        franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse",
        address_house_number="1",
        address_postal_code="8001",
        address_locality="Zürich",
        address_canton="ZH",
        phone="+41441234567",
        tax_id="CHE-123.456.789",
        vat_rate=Decimal("8.10"),
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def _make_invoiceable_item(db_session, tenant_id) -> StockItem:
    item = create_stock_item(
        db_session,
        tenant_id=tenant_id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia", condition=StockItemCondition.USED, vin="1HGCM82633A004352"
        ),
        actor_id=uuid.uuid4(),
    )
    return record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster",
            supplier_is_vat_registered=False,
            purchase_price=Decimal("20000.00"),
            purchase_date=dt.date(2026, 8, 1),
        ),
        actor_id=uuid.uuid4(),
    )


def test_legitimate_invoice_removes_item_from_active_list_and_emits_sold(db_session):
    dealership = _make_dealership(db_session)
    item = _make_invoiceable_item(db_session, dealership.id)
    assert item.is_invoiceable is True

    sold = apply_finance_invoice_issued(
        db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-0001"
    )
    assert sold.left_stock_at is not None
    # Never a 4th lifecycle value (FR-I-12 / reconciliation #1).
    assert sold.lifecycle_status.value in {"pipeline", "in_stock", "storno_pending"}

    message = db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.event_type == "inventory.stock_item.sold", OutboxMessage.aggregate_id == item.id
        )
    )
    assert message is not None

    rows, _, _, _ = list_stock_items(
        db_session, tenant_id=dealership.id, q=None, lifecycle_status=None,
        params=SortPageParams(limit=50, cursor=None, sort_fields=[]),
    )
    assert item.id not in {r.id for r in rows}


def test_invoice_against_a_non_invoiceable_item_raises_and_is_audited(db_session):
    dealership = _make_dealership(db_session)
    # Pipeline, never purchased — genuinely not invoiceable.
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Volkswagen Golf", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )

    with pytest.raises(ConflictError):
        apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-0002")

    audit = db_session.scalar(
        select(AuditEvent).where(AuditEvent.entity_type == "stock_item", AuditEvent.action == "invoicing_gate_alarm")
    )
    assert audit is not None
    assert audit.entity_id == item.id


def test_invoice_replay_on_an_already_sold_item_is_a_no_op(db_session):
    dealership = _make_dealership(db_session)
    item = _make_invoiceable_item(db_session, dealership.id)
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-0003")

    # Redelivery of the same fact — must not raise, must not emit again.
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="INV-0003")

    messages = list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == "inventory.stock_item.sold", OutboxMessage.aggregate_id == item.id
            )
        )
    )
    assert len(messages) == 1

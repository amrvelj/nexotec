"""WP-7 PR-3: purchase, landed cost, fiktiver Vorsteuerabzug (ADR-057,
Art. 28a MWSTG)."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.schemas.purchase import NotionalInputTaxOverrideRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate, StockItemRead, StockItemUpdate
from app.inventory.services.pipeline import promote_to_vehicle_mdm
from app.inventory.services.purchase import override_notional_input_tax, record_purchase
from app.inventory.services.stock_item import create_stock_item
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType


def _make_dealership(db_session, *, vat_rate: Decimal | None = Decimal("8.10")) -> Dealership:
    group = DealerGroup(name="Garage AG group")
    db_session.add(group)
    db_session.flush()
    dealership = Dealership(
        id=uuid.uuid4(),
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
        vat_rate=vat_rate,
    )
    db_session.add(dealership)
    db_session.commit()
    return dealership


def test_no_vat_treatment_field_exists_anywhere(db_session):
    """ADR-057 — enforced by name, on every schema a Stock item can be
    read/written through."""

    for schema in (StockItemRead, StockItemCreate, StockItemUpdate):
        field_names = set(schema.model_fields.keys())
        assert not any("vat_treatment" in f.lower() for f in field_names), (
            f"{schema.__name__} carries a vatTreatment-shaped field — ADR-057 forbids this everywhere."
        )


def test_record_purchase_from_private_individual_computes_notional_input_tax(db_session):
    dealership = _make_dealership(db_session, vat_rate=Decimal("8.10"))
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia",
            condition=StockItemCondition.USED,
            vehicle_id=uuid.uuid4(),
            vin="1HGCM82633A004352",
        ),
        actor_id=uuid.uuid4(),
    )
    updated = record_purchase(
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
    assert updated.notional_input_tax_applicable is True
    # 20000 * 8.10 / 108.10 = 1498.61...
    assert updated.notional_input_tax_amount == Decimal("1498.61")
    assert updated.is_invoiceable is True  # already in_stock (VIN known) + purchase now booked


def test_record_purchase_from_vat_registered_business_has_no_notional_input_tax(db_session):
    dealership = _make_dealership(db_session)
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia", condition=StockItemCondition.USED, vin="1HGCM82633A004352"
        ),
        actor_id=uuid.uuid4(),
    )
    updated = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Auto Grossist AG",
            supplier_is_vat_registered=True,
            purchase_price=Decimal("20000.00"),
            purchase_date=dt.date(2026, 8, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    assert updated.notional_input_tax_applicable is False
    assert updated.notional_input_tax_amount is None


def test_record_purchase_with_no_configured_vat_rate_leaves_amount_uncomputed(db_session):
    dealership = _make_dealership(db_session, vat_rate=None)
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia", condition=StockItemCondition.USED, vin="1HGCM82633A004352"
        ),
        actor_id=uuid.uuid4(),
    )
    updated = record_purchase(
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
    assert updated.notional_input_tax_applicable is True
    assert updated.notional_input_tax_amount is None


def test_purchase_booked_before_vin_arrival_becomes_invoiceable_on_promotion(db_session):
    """The other order (FR-I-02b): a trade-in's purchase is booked while
    it's still pipeline; is_invoiceable only flips once promotion also
    completes."""

    dealership = _make_dealership(db_session)
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Volkswagen Golf", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    updated = record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_name="Hans Muster",
            supplier_is_vat_registered=False,
            purchase_price=Decimal("8000.00"),
            purchase_date=dt.date(2026, 8, 1),
        ),
        actor_id=uuid.uuid4(),
    )
    assert updated.is_invoiceable is False  # still pipeline, no VIN yet

    promoted = promote_to_vehicle_mdm(db_session, item=updated, vin="WVWUWDJ62012T0KD3")
    assert promoted.is_invoiceable is True


def test_override_notional_input_tax_is_audited(db_session):
    dealership = _make_dealership(db_session, vat_rate=Decimal("8.10"))
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(
            vehicle_label="Škoda Octavia", condition=StockItemCondition.USED, vin="1HGCM82633A004352"
        ),
        actor_id=uuid.uuid4(),
    )
    record_purchase(
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

    actor_id = uuid.uuid4()
    overridden = override_notional_input_tax(
        db_session,
        item=item,
        data=NotionalInputTaxOverrideRequest(applicable=False, reason="Supplier later confirmed VAT registration."),
        actor_id=actor_id,
    )
    assert overridden.notional_input_tax_applicable is False
    assert overridden.notional_input_tax_overridden is True

    audit = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_type == "stock_item",
            AuditEvent.entity_id == item.id,
            AuditEvent.action == "notional_input_tax_override",
        )
    )
    assert audit is not None
    assert audit.actor_id == actor_id
    assert audit.reason == "Supplier later confirmed VAT registration."
    assert audit.before["notionalInputTaxApplicable"] is True
    assert audit.after["notionalInputTaxApplicable"] is False

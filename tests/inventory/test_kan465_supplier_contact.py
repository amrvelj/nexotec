"""KAN-465 — the purchase booking's supplier is a contact picked by search.

PRD-Stock: `supplier` is a reference + label (rule 2's three-column pattern);
Anto, 2026-11-21, option A: a contact is required for every new booking,
`supplierName` is removed from the request, a booking from before keeps its
typed name as the label with no contact. The contact holds the supplier role;
its VAT status prefills «Lieferant ist MWST-pflichtig», and a booking that
says otherwise is audited.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.contact.models.contact import Contact, ContactType
from app.contact.services.supplier_contact import vat_registered_prefill
from app.core.audit_model import AuditEvent
from app.core.consumer import consume_once
from app.core.errors import UnprocessableEntityError
from app.inventory.consumers import handle_contact_merged_message
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.reconciliation import CHECKS
from app.inventory.schemas.purchase import PurchaseStornoRequest, RecordPurchaseRequest
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.purchase import record_purchase, storno_purchase
from app.inventory.services.stock_item import create_stock_item
from app.inventory.services.supplier_labels import refresh_supplier_labels
from tests.inventory.test_inventory_purchase import _make_dealership
from tests.sales.test_sales_contact_merged_consumer import _merge
from tests.stock_support import supplier_contact

_VIN = "1HGCM82633A004352"


def _item(db_session, dealership) -> StockItem:
    return create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED, vin=_VIN),
        actor_id=uuid.uuid4(),
    )


def _book(db_session, item: StockItem, contact_id: uuid.UUID, *, vat_registered: bool = False) -> StockItem:
    return record_purchase(
        db_session,
        item=item,
        data=RecordPurchaseRequest(
            supplier_contact_id=contact_id,
            supplier_is_vat_registered=vat_registered,
            purchase_price=Decimal("20000.00"),
            purchase_date=dt.date(2026, 11, 1),
        ),
        actor_id=uuid.uuid4(),
    )


def _audits(db_session, item_id: uuid.UUID, action: str) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == item_id, AuditEvent.action == action)
        ).all()
    )


# --- the request carries a contact reference, not a string (exit criterion 5) ----------------


def test_the_purchase_request_carries_a_contact_reference_not_a_name():
    fields = RecordPurchaseRequest.model_fields
    assert "supplier_name" not in fields
    assert fields["supplier_contact_id"].annotation is uuid.UUID
    assert fields["supplier_contact_id"].is_required()
    with pytest.raises(ValueError):
        RecordPurchaseRequest.model_validate(
            {"supplierName": "Hans Muster", "supplierIsVatRegistered": False, "purchasePrice": "1", "purchaseDate": "2026-11-01"}
        )


# --- the booking stores the reference and its label ------------------------------------------


def test_the_booking_stores_the_contact_and_its_name_beside_it(db_session):
    dealership = _make_dealership(db_session)
    item = _item(db_session, dealership)
    contact_id = supplier_contact(db_session, tenant_id=dealership.id, name="Auto Grossist AG", business=True)

    booked = _book(db_session, item, contact_id, vat_registered=True)

    assert (booked.supplier_contact_id, booked.supplier_label) == (contact_id, "Auto Grossist AG")
    assert booked.supplier_label_refreshed_at is not None
    (row,) = _audits(db_session, item.id, "purchase_booked")
    assert row.after["supplierContactId"] == str(contact_id)
    assert row.after["supplierLabel"] == "Auto Grossist AG"


def test_a_contact_of_another_group_is_refused_as_unknown_and_nothing_is_written(db_session):
    dealership, elsewhere = _make_dealership(db_session), _make_dealership(db_session)
    item = _item(db_session, dealership)
    foreign = supplier_contact(db_session, tenant_id=elsewhere.id)

    for contact_id in (foreign, uuid.uuid4()):
        with pytest.raises(UnprocessableEntityError) as refused:
            _book(db_session, item, contact_id)
        assert refused.value.details == {"reason": "supplier_contact_unknown", "field": "supplierContactId"}
    db_session.rollback()
    stored = db_session.get(StockItem, item.id)
    assert (stored.purchase_price, stored.supplier_contact_id, stored.vehicle_id) == (None, None, None)


def test_a_contact_without_the_supplier_role_is_refused(db_session):
    dealership = _make_dealership(db_session)
    item = _item(db_session, dealership)
    customer_only = supplier_contact(db_session, tenant_id=dealership.id, roles=("customer",))

    with pytest.raises(UnprocessableEntityError) as refused:
        _book(db_session, item, customer_only)

    assert refused.value.details["reason"] == "supplier_role_missing"


def test_a_merged_contact_is_refused(db_session):
    dealership = _make_dealership(db_session)
    item = _item(db_session, dealership)
    survivor = supplier_contact(db_session, tenant_id=dealership.id, name="Hans Muster")
    duplicate = supplier_contact(db_session, tenant_id=dealership.id, name="Hans Muster")
    _merge(db_session, duplicate_id=duplicate, survivor_id=survivor, group_id=dealership.dealer_group_id)
    db_session.commit()

    with pytest.raises(UnprocessableEntityError) as refused:
        _book(db_session, item, duplicate)

    assert refused.value.details["reason"] == "supplier_contact_merged"


# --- the VAT prefill and its audited override (exit criterion 3) ------------------------------


@pytest.mark.parametrize(
    ("contact_type", "registered", "confirmed", "prefill"),
    [
        (ContactType.BUSINESS, True, True, True),
        (ContactType.BUSINESS, False, True, False),
        (ContactType.BUSINESS, False, False, None),
        (ContactType.INDIVIDUAL, False, False, False),
        (ContactType.INDIVIDUAL, True, True, True),
    ],
)
def test_the_prefill_reads_the_contacts_vat_status(contact_type, registered, confirmed, prefill):
    assert vat_registered_prefill(contact_type=contact_type, vat_registered=registered, vat_status_confirmed=confirmed) is prefill


def test_a_booking_against_the_contacts_confirmed_vat_status_is_audited_as_an_override(db_session):
    dealership = _make_dealership(db_session)
    item = _item(db_session, dealership)
    contact_id = supplier_contact(
        db_session, tenant_id=dealership.id, name="AMAG", business=True, vat_registered=True, vat_status_confirmed=True
    )

    _book(db_session, item, contact_id, vat_registered=False)

    (row,) = _audits(db_session, item.id, "supplier_vat_status_overridden")
    assert row.before == {"supplierContactId": str(contact_id), "supplierIsVatRegistered": True}
    assert row.after == {"supplierContactId": str(contact_id), "supplierIsVatRegistered": False}


@pytest.mark.parametrize(("business", "vat_registered"), [(False, False), (True, True), (True, False)])
def test_a_booking_that_follows_the_prefill_or_has_none_audits_no_override(db_session, business, vat_registered):
    dealership = _make_dealership(db_session)
    item = _item(db_session, dealership)
    # an unconfirmed private individual prefills «not registered»; an unconfirmed business prefills nothing
    contact_id = supplier_contact(db_session, tenant_id=dealership.id, name="Garage Muster", business=business)

    _book(db_session, item, contact_id, vat_registered=vat_registered)

    assert _audits(db_session, item.id, "supplier_vat_status_overridden") == []


# --- storno, the nightly refresh, a merge, reconciliation ------------------------------------


def test_a_storno_clears_the_supplier(db_session):
    dealership = _make_dealership(db_session)
    item = _book(db_session, _item(db_session, dealership), supplier_contact(db_session, tenant_id=dealership.id))

    stornoed = storno_purchase(db_session, item=item, data=PurchaseStornoRequest(reason="Falsch gebucht"), actor_id=uuid.uuid4())

    assert (stornoed.supplier_contact_id, stornoed.supplier_label, stornoed.supplier_label_refreshed_at) == (None, None, None)


def test_a_renamed_contact_reaches_the_item_overnight_without_a_version_bump(db_session):
    dealership = _make_dealership(db_session)
    contact_id = supplier_contact(db_session, tenant_id=dealership.id, name="Hans Muster")
    item = _book(db_session, _item(db_session, dealership), contact_id)
    version = item.version
    legacy = _item_with_typed_label(db_session, dealership)
    db_session.get(Contact, contact_id).last_name = "Meier"
    db_session.commit()

    report = refresh_supplier_labels(db_session)

    db_session.expire_all()
    stored = db_session.get(StockItem, item.id)
    assert (stored.supplier_label, stored.version) == ("Hans Meier", version)
    assert report.changed == [(item.id, "Hans Muster", "Hans Meier")]
    # A booking from before KAN-465 keeps the name typed then (Anto, option A).
    assert db_session.get(StockItem, legacy.id).supplier_label == "Privat, Hans Muster"

    again = refresh_supplier_labels(db_session)
    assert (again.changed, again.restamped) == ([], 1)


def _item_with_typed_label(db_session, dealership) -> StockItem:
    item = create_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED),
        actor_id=uuid.uuid4(),
    )
    item.supplier_label = "Privat, Hans Muster"
    db_session.commit()
    return item


def test_a_contact_merge_moves_the_supplier_to_the_survivor_with_its_name(db_session):
    dealership = _make_dealership(db_session)
    survivor = supplier_contact(db_session, tenant_id=dealership.id, name="Garage Keller", business=True)
    duplicate = supplier_contact(db_session, tenant_id=dealership.id, name="Garage Keler", business=True)
    item = _book(db_session, _item(db_session, dealership), duplicate)
    message = _merge(db_session, duplicate_id=duplicate, survivor_id=survivor, group_id=dealership.dealer_group_id)
    db_session.commit()

    assert consume_once(db_session, message=message, consumer_name="inventory.contact_merged", handler=handle_contact_merged_message)

    db_session.expire_all()
    stored = db_session.get(StockItem, item.id)
    assert (stored.supplier_contact_id, stored.supplier_label) == (survivor, "Garage Keller")


def test_reconciliation_checks_the_supplier_reference():
    assert "stock_item.supplier_contact_id -> contact.id" in {check.label for check in CHECKS}

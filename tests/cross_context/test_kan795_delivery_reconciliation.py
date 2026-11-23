"""KAN-795 (rule 10; ADR-107, ADR-131): the nightly reconciliation checks the
two references KAN-217 PR-5 added to integration's delivery record.

- `document_link_id` names platform's `document_link` row — checked in
  integration's own run (platform sits below integration).
- `customer_id` names a customer — checked in **Customers'** run, through
  integration's builder, since integration sits below Customers and may not
  ask `customer.public`.

Both are nullable: an e-mail carries no link, and the e-mails sent before PR-5
name no customer. A null is absent, never an orphan. The rows span three
contexts' tables, hence cross_context."""

import datetime as dt
import hashlib
import uuid

from app.core.reconciliation import find_orphans
from app.customer import reconciliation as customer_reconciliation
from app.customer.models.customer import Customer, CustomerType, Language
from app.integration import reconciliation as integration_reconciliation
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.models.mail import DeliveryChannel, DeliveryStatus, MessageDelivery
from app.integration.models.provider import IntegrationProvider
from app.platform.documents.models.document_link import DocumentLink
from app.platform.organisation.models.dealership import DealerGroup, Dealership, FranchiseType
from tests.dealership_support import valid_uid

_LINK_CHECK = "integration_message_delivery.document_link_id -> document_link.id"
_CUSTOMER_CHECK = "integration_message_delivery.customer_id -> customer.id"


def _check(checks, label):
    (check,) = [check for check in checks if check.label == label]
    return check


def _scene(db):
    group = DealerGroup(name=f"Group {uuid.uuid4().hex[:6]}")
    db.add(group)
    db.flush()
    dealership = Dealership(
        dealer_group_id=group.id, legal_name="Garage Muster AG", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    db.add(dealership)
    db.flush()
    provider = IntegrationProvider(
        provider_code=f"sms_{uuid.uuid4().hex[:6]}", category="messaging", display_name="SMS",
        auth_type="password", required_secret_slots=[], required_config_keys=[], capability_codes=[],
    )
    db.add(provider)
    db.flush()
    connection = IntegrationConnection(
        tenant_id=dealership.id, scope=ConnectionScope.TENANT, provider_id=provider.id, display_name="SMS",
        environment=ConnectionEnvironment.PRODUCTION, config={},
    )
    db.add(connection)
    customer = Customer(
        group_id=group.id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.DE, first_name="Anna", last_name="Muster",
    )
    db.add(customer)
    link = DocumentLink(
        tenant_id=dealership.id, message_type="offer", document_id=uuid.uuid4(),
        token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=14),
    )
    db.add(link)
    db.flush()
    return dealership, connection, customer, link


def _delivery(db, dealership, connection, *, customer_id, document_link_id) -> MessageDelivery:
    delivery = MessageDelivery(
        tenant_id=dealership.id, channel=DeliveryChannel.SMS, connection_id=connection.id,
        recipients=["+41790000795"], body_text="Ihre Offerte", document_type="offer", document_id=uuid.uuid4(),
        document_link_id=document_link_id, customer_id=customer_id,
        customer_label="Anna Muster" if customer_id else None,
        customer_label_refreshed_at=dt.datetime.now(dt.UTC) if customer_id else None,
        author_user_id=uuid.uuid4(), status=DeliveryStatus.SENT, sent_at=dt.datetime.now(dt.UTC),
    )
    db.add(delivery)
    db.flush()
    return delivery


def test_a_delivery_naming_no_document_link_is_an_orphan_in_integrations_run(db_session):
    dealership, connection, customer, link = _scene(db_session)
    valid = _delivery(db_session, dealership, connection, customer_id=customer.id, document_link_id=link.id)
    stray_link = uuid.uuid4()
    stray = _delivery(db_session, dealership, connection, customer_id=customer.id, document_link_id=stray_link)
    no_link = _delivery(db_session, dealership, connection, customer_id=None, document_link_id=None)

    found = find_orphans(db_session, _check(integration_reconciliation.CHECKS, _LINK_CHECK))

    assert found == [(stray.id, stray_link)]
    assert {valid.id, no_link.id}.isdisjoint({row for row, _ in found})


def test_a_delivery_naming_no_customer_is_an_orphan_in_customers_run(db_session):
    dealership, connection, customer, link = _scene(db_session)
    valid = _delivery(db_session, dealership, connection, customer_id=customer.id, document_link_id=link.id)
    stray_customer = uuid.uuid4()
    stray = _delivery(db_session, dealership, connection, customer_id=stray_customer, document_link_id=link.id)
    before_pr5 = _delivery(db_session, dealership, connection, customer_id=None, document_link_id=link.id)

    found = find_orphans(db_session, _check(customer_reconciliation.CHECKS, _CUSTOMER_CHECK))

    assert found == [(stray.id, stray_customer)]
    assert {valid.id, before_pr5.id}.isdisjoint({row for row, _ in found})


def test_the_customer_check_runs_in_customers_run_not_integrations():
    # ADR-131: integration cannot ask customer.public, so the owner runs it.
    assert _CUSTOMER_CHECK not in {check.label for check in integration_reconciliation.CHECKS}
    assert _CUSTOMER_CHECK in {check.label for check in customer_reconciliation.CHECKS}

"""KAN-139: before KAN-99, allocate_vehicle_party closed the open holder of
(vehicle, role) without a group filter, so group A could close group B's
holder and stamp the audit row and the `unlinked` event with group A. The
detection is read-only; these tests pin how each stamp is classified.

Rows are written directly rather than through allocate_vehicle_party:
since KAN-99 the service can no longer produce a cross-group close, and
that is exactly the history this detection exists to find.
"""

import uuid

from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.customer.models.customer import Customer, CustomerType, Language
from app.customer.reconciliation import CloseCategory, find_cross_group_vehicle_party_closes
from app.platform.models.dealership import DealerGroup, Dealership, FranchiseType


def _group(db_session, name: str) -> DealerGroup:
    group = DealerGroup(name=name)
    db_session.add(group)
    db_session.flush()
    return group


def _dealership(db_session, group: DealerGroup, license_number: str) -> Dealership:
    dealership = Dealership(
        dealer_group_id=group.id, legal_name=f"Garage {license_number}", dealer_license_number=license_number,
        license_state="ZH", franchise_type=FranchiseType.INDEPENDENT, address_street="Bahnhofstrasse",
        address_house_number="1", address_postal_code="8001", address_locality="Zürich", address_canton="ZH",
        phone="+41441234567", tax_id=f"CHE-{license_number}.789",
    )
    db_session.add(dealership)
    db_session.flush()
    return dealership


def _customer(db_session, group: DealerGroup) -> Customer:
    customer = Customer(
        group_id=group.id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def _close_audit(db_session, *, customer_id: uuid.UUID, stamped: uuid.UUID | None) -> AuditEvent:
    event = AuditEvent(
        entity_type="customer", entity_id=customer_id, tenant_id=stamped, action="vehicle_party_remove",
        actor_id=uuid.uuid4(), before={"role": "owner"}, after={"effectiveTo": utcnow().isoformat()},
    )
    db_session.add(event)
    db_session.flush()
    return event


def _unlinked_event(db_session, *, customer_id: object, stamped: uuid.UUID | None) -> OutboxMessage:
    message = OutboxMessage(
        event_type="customer.vehicle_party.unlinked", occurred_at=utcnow(), tenant_id=stamped, producer="customer",
        aggregate_type="vehicle_party", aggregate_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        payload={"vehiclePartyId": str(uuid.uuid4()), "customerId": customer_id, "role": "owner"},
    )
    db_session.add(message)
    db_session.flush()
    return message


def _categories(report) -> dict[uuid.UUID, CloseCategory]:
    return {row.row_id: row.category for row in report.rows}


def test_a_close_stamped_with_another_group_is_a_finding(db_session):
    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    holder_in_b = _customer(db_session, group_b)
    audit = _close_audit(db_session, customer_id=holder_in_b.id, stamped=group_a.id)
    event = _unlinked_event(db_session, customer_id=str(holder_in_b.id), stamped=group_a.id)

    report = find_cross_group_vehicle_party_closes(db_session)

    assert _categories(report) == {audit.id: CloseCategory.CROSS_GROUP, event.id: CloseCategory.CROSS_GROUP}
    assert {(f.source, f.customer_group_id, f.stamped_group_id) for f in report.findings} == {
        ("audit_event", group_b.id, group_a.id),
        ("outbox_message", group_b.id, group_a.id),
    }


def test_a_close_stamped_with_the_holders_own_group_is_not(db_session):
    group = _group(db_session, "A")
    holder = _customer(db_session, group)
    audit = _close_audit(db_session, customer_id=holder.id, stamped=group.id)
    event = _unlinked_event(db_session, customer_id=str(holder.id), stamped=group.id)

    report = find_cross_group_vehicle_party_closes(db_session)

    assert _categories(report) == {audit.id: CloseCategory.SAME_GROUP, event.id: CloseCategory.SAME_GROUP}
    assert report.findings == []


def test_a_dealership_stamp_counts_as_the_dealerships_group(db_session):
    """Before WP-3 PR-2 (026d3bb, 2026-08-25) customers were dealership-
    scoped and the close was stamped with the dealership's tenant id. A
    dealership in the holder's group is a same-group close; one in another
    group is a cross-group close."""

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    sister_in_b = _dealership(db_session, group_b, "B-1")
    dealership_in_a = _dealership(db_session, group_a, "A-1")
    holder_in_b = _customer(db_session, group_b)
    same = _close_audit(db_session, customer_id=holder_in_b.id, stamped=sister_in_b.id)
    cross = _close_audit(db_session, customer_id=holder_in_b.id, stamped=dealership_in_a.id)

    report = find_cross_group_vehicle_party_closes(db_session)

    assert _categories(report) == {
        same.id: CloseCategory.SAME_GROUP_DEALERSHIP_STAMP,
        cross.id: CloseCategory.CROSS_GROUP,
    }
    [finding] = report.findings
    assert finding.stamped_group_id == group_a.id


def test_what_cannot_be_resolved_is_reported_never_guessed(db_session):
    group = _group(db_session, "A")
    holder = _customer(db_session, group)
    missing_customer = _close_audit(db_session, customer_id=uuid.uuid4(), stamped=group.id)
    unknown_stamp = _close_audit(db_session, customer_id=holder.id, stamped=uuid.uuid4())
    no_stamp = _close_audit(db_session, customer_id=holder.id, stamped=None)
    bad_payload = _unlinked_event(db_session, customer_id="not-a-uuid", stamped=group.id)

    report = find_cross_group_vehicle_party_closes(db_session)

    assert _categories(report) == {
        missing_customer.id: CloseCategory.UNRESOLVED,
        unknown_stamp.id: CloseCategory.UNRESOLVED,
        no_stamp.id: CloseCategory.UNRESOLVED,
        bad_payload.id: CloseCategory.UNRESOLVED,
    }
    assert report.findings == []
    assert len(report.unresolved) == 4


def test_other_audit_actions_and_event_types_are_ignored(db_session):
    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    holder_in_b = _customer(db_session, group_b)
    db_session.add(AuditEvent(
        entity_type="customer", entity_id=holder_in_b.id, tenant_id=group_a.id, action="vehicle_party_add",
        actor_id=uuid.uuid4(),
    ))
    linked = _unlinked_event(db_session, customer_id=str(holder_in_b.id), stamped=group_a.id)
    linked.event_type = "customer.vehicle_party.linked"
    db_session.flush()

    assert find_cross_group_vehicle_party_closes(db_session).rows == []


def test_detection_writes_nothing(db_session):
    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    holder_in_b = _customer(db_session, group_b)
    _close_audit(db_session, customer_id=holder_in_b.id, stamped=group_a.id)
    _unlinked_event(db_session, customer_id=str(holder_in_b.id), stamped=group_a.id)
    db_session.commit()

    def counts():
        return tuple(
            db_session.scalar(select(func.count()).select_from(model))
            for model in (AuditEvent, OutboxMessage, Customer, DealerGroup, Dealership)
        )

    before = counts()
    find_cross_group_vehicle_party_closes(db_session)

    assert not db_session.new and not db_session.dirty and not db_session.deleted
    db_session.rollback()
    assert counts() == before


def test_the_operator_report_names_each_finding(db_session):
    from scripts.detect_cross_group_vehicle_party_closes import render

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    holder_in_b = _customer(db_session, group_b)
    audit = _close_audit(db_session, customer_id=holder_in_b.id, stamped=group_a.id)

    text = render(find_cross_group_vehicle_party_closes(db_session))

    assert "audit_event: 1 close(s)" in text and "cross_group=1" in text
    assert "outbox_message: 0 close(s)" in text
    assert f"audit_event {audit.id}" in text and f"(group {group_b.id})" in text and f"(group {group_a.id})" in text

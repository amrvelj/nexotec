"""KAN-176: before KAN-99, the trade-in path passed a request-body customer
id straight to allocate_vehicle_party, which did not resolve it in the
caller's group, so group A could link group B's customer to a vehicle and
stamp the `vehicle_party_add` audit row and the `linked` event with group
A. The detection mirrors KAN-139's for closes, with the same categories;
these tests pin how each stamp is classified and that the operator script
runs both detections in one read-only transaction.

Rows are written directly rather than through allocate_vehicle_party:
since KAN-99 the service 404s a customer outside the caller's group, and
that is exactly the history this detection exists to find. The fixtures
are KAN-139's (test_customer_cross_group_close_detection).
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.customer.reconciliation import (
    StampCategory,
    find_cross_group_vehicle_party_closes,
    find_cross_group_vehicle_party_links,
)
from tests.test_customer_cross_group_close_detection import (
    _POSTGRES_ONLY,
    _categories,
    _close_audit,
    _customer,
    _dealership,
    _group,
    _unlinked_event,
)


def _link_audit(db_session, *, customer_id: uuid.UUID, stamped: uuid.UUID | None) -> AuditEvent:
    event = AuditEvent(
        entity_type="customer", entity_id=customer_id, tenant_id=stamped, action="vehicle_party_add",
        actor_id=uuid.uuid4(),
        after={"vehicleId": str(uuid.uuid4()), "role": "owner", "effectiveFrom": utcnow().isoformat()},
    )
    db_session.add(event)
    db_session.flush()
    return event


def _linked_event(db_session, *, customer_id: object, stamped: uuid.UUID | None) -> OutboxMessage:
    message = OutboxMessage(
        event_type="customer.vehicle_party.linked", occurred_at=utcnow(), tenant_id=stamped, producer="customer",
        aggregate_type="vehicle_party", aggregate_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
        payload={
            "vehiclePartyId": str(uuid.uuid4()), "vehicleId": str(uuid.uuid4()), "customerId": customer_id,
            "role": "owner", "effectiveFrom": utcnow().isoformat(), "effectiveTo": None,
        },
    )
    db_session.add(message)
    db_session.flush()
    return message


def test_a_link_stamped_with_another_group_is_a_finding(db_session):
    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    audit = _link_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)
    event = _linked_event(db_session, customer_id=str(customer_in_b.id), stamped=group_a.id)

    report = find_cross_group_vehicle_party_links(db_session)

    assert report.operation == "link"
    assert _categories(report) == {audit.id: StampCategory.CROSS_GROUP, event.id: StampCategory.CROSS_GROUP}
    assert {(f.source, f.customer_group_id, f.stamped_group_id) for f in report.findings} == {
        ("audit_event", group_b.id, group_a.id),
        ("outbox_message", group_b.id, group_a.id),
    }


def test_a_link_stamped_with_the_customers_own_group_is_not(db_session):
    group = _group(db_session, "A")
    customer = _customer(db_session, group)
    audit = _link_audit(db_session, customer_id=customer.id, stamped=group.id)
    event = _linked_event(db_session, customer_id=str(customer.id), stamped=group.id)

    report = find_cross_group_vehicle_party_links(db_session)

    assert _categories(report) == {audit.id: StampCategory.SAME_GROUP, event.id: StampCategory.SAME_GROUP}
    assert report.findings == []


def test_a_dealership_stamp_counts_as_the_dealerships_group(db_session):
    """Before WP-3 PR-2 (026d3bb, 2026-08-25) customers were dealership-
    scoped and a link was stamped with the dealership's tenant id. A
    dealership in the customer's group is a same-group link; one in
    another group is a cross-group link."""

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    sister_in_b = _dealership(db_session, group_b, "B-1")
    dealership_in_a = _dealership(db_session, group_a, "A-1")
    customer_in_b = _customer(db_session, group_b)
    same = _link_audit(db_session, customer_id=customer_in_b.id, stamped=sister_in_b.id)
    cross = _link_audit(db_session, customer_id=customer_in_b.id, stamped=dealership_in_a.id)

    report = find_cross_group_vehicle_party_links(db_session)

    assert _categories(report) == {
        same.id: StampCategory.SAME_GROUP_DEALERSHIP_STAMP,
        cross.id: StampCategory.CROSS_GROUP,
    }
    [finding] = report.findings
    assert finding.stamped_group_id == group_a.id


def test_what_cannot_be_resolved_is_reported_never_guessed(db_session):
    group = _group(db_session, "A")
    customer = _customer(db_session, group)
    missing_customer = _link_audit(db_session, customer_id=uuid.uuid4(), stamped=group.id)
    unknown_stamp = _link_audit(db_session, customer_id=customer.id, stamped=uuid.uuid4())
    no_stamp = _link_audit(db_session, customer_id=customer.id, stamped=None)
    bad_payload = _linked_event(db_session, customer_id="not-a-uuid", stamped=group.id)

    report = find_cross_group_vehicle_party_links(db_session)

    assert _categories(report) == {
        missing_customer.id: StampCategory.UNRESOLVED,
        unknown_stamp.id: StampCategory.UNRESOLVED,
        no_stamp.id: StampCategory.UNRESOLVED,
        bad_payload.id: StampCategory.UNRESOLVED,
    }
    assert report.findings == []
    assert len(report.unresolved) == 4


def test_closes_and_links_are_classified_apart(db_session):
    """A close is never counted as a link and a link never as a close: the
    two detections read disjoint rows, so each report's count is its own."""

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    close_audit = _close_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)
    unlinked = _unlinked_event(db_session, customer_id=str(customer_in_b.id), stamped=group_a.id)
    link_audit = _link_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)
    linked = _linked_event(db_session, customer_id=str(customer_in_b.id), stamped=group_a.id)

    assert set(_categories(find_cross_group_vehicle_party_links(db_session))) == {link_audit.id, linked.id}
    assert set(_categories(find_cross_group_vehicle_party_closes(db_session))) == {close_audit.id, unlinked.id}


@_POSTGRES_ONLY
def test_detection_runs_inside_a_read_only_transaction(db_session):
    """Read-only is enforced, not asserted: Postgres rejects any write a
    READ ONLY transaction attempts, flushed or not."""

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    _link_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)
    _linked_event(db_session, customer_id=str(customer_in_b.id), stamped=group_a.id)
    db_session.commit()

    db_session.execute(text("SET TRANSACTION READ ONLY"))
    report = find_cross_group_vehicle_party_links(db_session)
    assert len(report.findings) == 2
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    db_session.rollback()


@_POSTGRES_ONLY
def test_the_operator_script_runs_both_detections_and_fails_on_a_link_finding(db_session, monkeypatch, capsys):
    """One run, one READ ONLY transaction, both reports: a cross-group link
    alone makes the exit status 1, exactly as a cross-group close does."""

    import scripts.detect_cross_group_vehicle_party_closes as script

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    audit = _link_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)
    db_session.commit()

    sessions = []

    def session_factory():
        session = sessionmaker(bind=db_session.get_bind())()
        sessions.append(session)
        return session

    monkeypatch.setattr(script, "SessionLocal", session_factory)

    assert script.main() == 1
    out = capsys.readouterr().out
    assert "KAN-139 cross-group vehicle-party close detection" in out
    assert "KAN-176 cross-group vehicle-party link detection" in out
    assert "audit_event: 0 close(s)" in out
    assert "audit_event: 1 link(s)" in out and "cross_group=1" in out
    assert f"audit_event {audit.id}" in out
    assert all(not session.in_transaction() for session in sessions)


@_POSTGRES_ONLY
def test_the_operator_script_exits_zero_when_both_reports_are_clean(db_session, monkeypatch, capsys):
    import scripts.detect_cross_group_vehicle_party_closes as script

    group = _group(db_session, "A")
    customer = _customer(db_session, group)
    _link_audit(db_session, customer_id=customer.id, stamped=group.id)
    _close_audit(db_session, customer_id=customer.id, stamped=group.id)
    db_session.commit()

    monkeypatch.setattr(script, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    assert script.main() == 0
    out = capsys.readouterr().out
    assert "audit_event: 1 close(s)" in out and "audit_event: 1 link(s)" in out
    assert "CROSS-GROUP: 0" in out


def test_the_operator_report_names_each_link_finding(db_session):
    from scripts.detect_cross_group_vehicle_party_closes import render

    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    audit = _link_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)

    out = render(find_cross_group_vehicle_party_links(db_session))

    assert out.startswith("KAN-176 cross-group vehicle-party link detection")
    assert "audit_event: 1 link(s)" in out and "cross_group=1" in out
    assert "outbox_message: 0 link(s)" in out
    assert f"audit_event {audit.id}" in out and f"(group {group_b.id})" in out and f"(group {group_a.id})" in out


def test_a_close_alone_yields_no_link_row(db_session):
    # Not vacuous: a close alone produces no link row, so the link report is
    # empty and clean — the findings above come from link rows only.
    group_a, group_b = _group(db_session, "A"), _group(db_session, "B")
    customer_in_b = _customer(db_session, group_b)
    _close_audit(db_session, customer_id=customer_in_b.id, stamped=group_a.id)

    report = find_cross_group_vehicle_party_links(db_session)

    assert report.rows == [] and report.findings == [] and report.unresolved == []

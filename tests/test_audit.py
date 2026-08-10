import uuid

from app.core.audit import list_audit_events, record_audit_event


def test_record_audit_event_persists_before_after(db_session):
    entity_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    record_audit_event(
        db_session,
        entity_type="customer",
        entity_id=entity_id,
        tenant_id=tenant_id,
        action="update",
        actor_id=actor_id,
        before={"email": "old@example.ch"},
        after={"email": "new@example.ch"},
        reason="customer requested change",
    )
    db_session.commit()

    events = list_audit_events(db_session, entity_type="customer", entity_id=entity_id, tenant_id=tenant_id)
    assert len(events) == 1
    event = events[0]
    assert event.action == "update"
    assert event.before == {"email": "old@example.ch"}
    assert event.after == {"email": "new@example.ch"}
    assert event.reason == "customer requested change"
    assert event.actor_id == actor_id


def test_list_audit_events_is_scoped_to_tenant_when_given(db_session):
    entity_id = uuid.uuid4()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    record_audit_event(
        db_session, entity_type="vehicle", entity_id=entity_id, tenant_id=tenant_a,
        action="create", actor_id=uuid.uuid4(),
    )
    db_session.commit()

    assert len(list_audit_events(db_session, entity_type="vehicle", entity_id=entity_id, tenant_id=tenant_a)) == 1
    assert len(list_audit_events(db_session, entity_type="vehicle", entity_id=entity_id, tenant_id=tenant_b)) == 0


def test_list_audit_events_orders_chronologically(db_session):
    entity_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    for action in ("create", "update", "update"):
        record_audit_event(
            db_session, entity_type="vehicle", entity_id=entity_id, tenant_id=tenant_id,
            action=action, actor_id=uuid.uuid4(),
        )
    db_session.commit()

    events = list_audit_events(db_session, entity_type="vehicle", entity_id=entity_id, tenant_id=tenant_id)
    assert [e.action for e in events] == ["create", "update", "update"]
    assert events[0].created_at <= events[1].created_at <= events[2].created_at

"""KAN-222 (FR-A-22, D-A-40): an integration nearing its expiry reaches the
Betrieb's managers as an in-app `administration` notification.

Integration's daily check states the fact (`integration.connection.
expiry_approaching`) with its record row; platform's consumer raises
`administration.integration_expiring` for each **active** manager — homed in
the Betrieb or by an active membership — and for nobody else. The family is
switchable: a manager who switched it off gets nothing.
"""

import datetime as dt
import uuid

from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.integration.models.connection import IntegrationConnection
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import notifications as expiry_service
from app.platform.consumers import handle_connection_expiry_approaching_message
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.models.notification import (
    Notification,
    NotificationFamily,
    NotificationTiming,
    UserNotificationPreference,
)
from app.platform.models.user import User, UserRole, UserStatus
from tests.test_inventory_invoicing_gate import _make_dealership

TODAY = dt.date(2026, 11, 14)


def _user(db_session, dealership_id, *, manager: bool, status=UserStatus.ACTIVE) -> User:
    user = User(
        tenant_id=dealership_id, first_name="Ada", last_name="Lovelace",
        email=f"u-{uuid.uuid4().hex[:8]}@example.ch", role=UserRole.SALES, access_roles=[],
        is_dealer_manager=manager, status=status, auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.commit()
    return user


def _fact(db_session, dealership_id, *, days=14) -> tuple[OutboxMessage, uuid.UUID]:
    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="Demo", auth_type="none",
        required_secret_slots=[], capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    connection = connection_service.create_connection(
        db_session, tenant_id=dealership_id,
        data=ConnectionCreate(provider_id=provider.id, display_name="Demo", environment="sandbox"),
        actor_id=None,
    )
    row = db_session.get(IntegrationConnection, connection.id)
    row.expires_at = dt.datetime.combine(TODAY + dt.timedelta(days=days), dt.time(9), tzinfo=dt.UTC)
    db_session.commit()
    expiry_service.check_and_send_expiry_warnings(db_session, today=TODAY)
    message = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == expiry_service.EXPIRY_APPROACHING_EVENT)
    ).one()
    return message, connection.id


def test_each_active_manager_gets_the_administration_notification(db_session):
    dealership = _make_dealership(db_session)
    sister = _make_dealership(db_session)
    home_manager = _user(db_session, dealership.id, manager=True)
    _user(db_session, dealership.id, manager=False)  # a colleague, not a manager
    _user(db_session, dealership.id, manager=True, status=UserStatus.SUSPENDED)
    member_manager = _user(db_session, sister.id, manager=False)
    db_session.add(
        DealershipMembership(user_id=member_manager.id, dealership_id=dealership.id, is_dealer_manager=True)
    )
    db_session.commit()
    message, connection_id = _fact(db_session, dealership.id)

    handle_connection_expiry_approaching_message(db_session, message)
    db_session.commit()

    rows = db_session.scalars(select(Notification).where(Notification.subject_id == connection_id)).all()
    assert {r.user_id for r in rows} == {home_manager.id, member_manager.id}
    for row in rows:
        assert row.event_code == "administration.integration_expiring"
        assert row.dealership_id == dealership.id
        assert row.link == "/administration/integrations"
        assert row.params == {"days": 14}
        assert row.delivered_at is not None  # immediate by default


def test_a_manager_who_switched_the_family_off_gets_nothing(db_session):
    dealership = _make_dealership(db_session)
    manager = _user(db_session, dealership.id, manager=True)
    db_session.add(
        UserNotificationPreference(
            user_id=manager.id, family=NotificationFamily.ADMINISTRATION, timing=NotificationTiming.OFF,
            in_app=True, push=True,
        )
    )
    db_session.commit()
    message, connection_id = _fact(db_session, dealership.id)

    handle_connection_expiry_approaching_message(db_session, message)
    db_session.commit()

    assert db_session.scalars(select(Notification).where(Notification.subject_id == connection_id)).all() == []


def test_a_fact_naming_two_betriebe_is_refused(db_session):
    dealership = _make_dealership(db_session)
    message, _ = _fact(db_session, dealership.id)
    message.payload = {**message.payload, "dealershipId": str(uuid.uuid4())}

    try:
        handle_connection_expiry_approaching_message(db_session, message)
    except ValueError as exc:
        assert "names two Betriebe" in str(exc)
    else:
        raise AssertionError("a fact naming two Betriebe must be refused")
    finally:
        db_session.rollback()

"""KAN-218 (Anto, 2026-11-14, Q2 A; FR-A-45 "accessible via export only"):
an offboarded Betrieb drops out of every group read from its offboarding
date. Its sisters no longer see its stock in the group stock listing, nor
find a car of theirs in its stock (the customer 360's Vehicles tab), nor read
its name or its users' names in a group customer's history; an active sister
still is in each.
"""

import types
import uuid

from app.core.auth import AccessRole, Principal
from app.core.base import utcnow
from app.customer.services import customer as customer_service
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.public import get_stock_items_for_vehicles
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.group_listing import list_group_stock_dealerships
from app.platform.models.dealership import DealerGroup, Dealership, DealershipStatus
from app.platform.models.user import User, UserRole, UserStatus
from app.platform.public import list_group_read_dealerships
from tests.stock_support import create_in_stock_item
from tests.test_inventory_group_listing import _make_dealership, _params
from tests.test_inventory_group_listing import list_group_stock_items as _group_stock


def _group(db_session) -> tuple[DealerGroup, Dealership, Dealership, Dealership]:
    """A group with its read on: the viewer, a sister that leaves, a sister that stays."""

    group = DealerGroup(name="Gruppe Muster", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()
    viewer = _make_dealership(db_session, group, license_number="ZH-1")
    leaving = _make_dealership(db_session, group, license_number="ZH-2")
    staying = _make_dealership(db_session, group, license_number="ZH-3")
    return group, viewer, leaving, staying


def _offboarded(db_session, dealership: Dealership) -> None:
    dealership.status = DealershipStatus.OFFBOARDED
    dealership.offboarded_at = utcnow()
    db_session.commit()


def _car(db_session, dealership: Dealership, label: str, vin: str) -> uuid.UUID:
    item = create_in_stock_item(
        db_session,
        tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label=label, condition=StockItemCondition.USED, vin=vin),
        actor_id=None,
    )
    assert item.vehicle_id is not None
    return item.vehicle_id


def _principal(dealership: Dealership) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        memberships=frozenset({dealership.id}), active_location_id=None, roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=False,
    )


def _user(db_session, dealership: Dealership, first_name: str) -> User:
    user = User(
        tenant_id=dealership.id, first_name=first_name, last_name="Muster",
        email=f"{first_name.lower()}-{uuid.uuid4().hex[:8]}@example.ch", role=UserRole.SALES, access_roles=[],
        is_dealer_manager=False, status=UserStatus.ACTIVE, auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_the_group_roster_drops_an_offboarded_betrieb(db_session):
    group, viewer, leaving, staying = _group(db_session)
    assert {ref.id for ref in list_group_read_dealerships(db_session, group_id=group.id)} == {
        viewer.id, leaving.id, staying.id,
    }

    _offboarded(db_session, leaving)

    assert {ref.id for ref in list_group_read_dealerships(db_session, group_id=group.id)} == {viewer.id, staying.id}
    filter_options = list_group_stock_dealerships(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True
    )
    assert {ref.id for ref in filter_options} == {viewer.id, staying.id}


def test_a_sister_no_longer_sees_the_offboarded_betriebs_stock(db_session):
    group, viewer, leaving, staying = _group(db_session)
    _car(db_session, viewer, "Seat Leon", "WVWZZZ1KZAW218001")
    _car(db_session, leaving, "VW Golf", "WVWZZZ1KZAW218002")
    _car(db_session, staying, "Škoda Octavia", "WVWZZZ1KZAW218003")

    def listed() -> set[str]:
        rows = _group_stock(
            db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
            params=_params(),
        )
        return {item.vehicle_label for item, _ref in rows}

    assert listed() == {"Seat Leon", "VW Golf", "Škoda Octavia"}
    _offboarded(db_session, leaving)
    assert listed() == {"Seat Leon", "Škoda Octavia"}
    # Asking for the offboarded Betrieb by name matches nothing, never a 404.
    rows = _group_stock(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True,
        params=_params(), dealership_ids=[leaving.id],
    )
    assert rows == []


def test_the_customer_360_no_longer_finds_a_car_in_the_offboarded_betriebs_stock(db_session):
    _, viewer, leaving, staying = _group(db_session)
    in_leaving = _car(db_session, leaving, "VW Golf", "WVWZZZ1KZAW218004")
    in_staying = _car(db_session, staying, "Škoda Octavia", "WVWZZZ1KZAW218005")
    principal = _principal(viewer)
    assert set(get_stock_items_for_vehicles(db_session, principal=principal, vehicle_ids=[in_leaving, in_staying])) == {
        in_leaving, in_staying,
    }

    _offboarded(db_session, leaving)

    assert set(get_stock_items_for_vehicles(db_session, principal=principal, vehicle_ids=[in_leaving, in_staying])) == {
        in_staying,
    }


def test_a_group_customers_history_no_longer_names_the_offboarded_betrieb_or_its_users(db_session):
    group, viewer, leaving, staying = _group(db_session)
    left_user = _user(db_session, leaving, "Lea")
    stayed_user = _user(db_session, staying, "Sven")
    events = [
        types.SimpleNamespace(actor_id=left_user.id, before=None, after={"dealershipId": str(leaving.id)}),
        types.SimpleNamespace(actor_id=stayed_user.id, before=None, after={"dealership_id": str(staying.id)}),
    ]

    def labels() -> dict[str, str]:
        return customer_service.history_labels(db_session, group_id=group.id, dealership_id=viewer.id, events=events)

    before = labels()
    assert {str(leaving.id), str(left_user.id), str(staying.id), str(stayed_user.id)} <= set(before)

    _offboarded(db_session, leaving)

    after = labels()
    assert str(leaving.id) not in after and str(left_user.id) not in after
    assert after[str(staying.id)] == staying.legal_name
    assert after[str(stayed_user.id)] == "Sven Muster"

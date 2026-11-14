"""KAN-538 (rule 7): `inventory.public.get_stock_items_for_vehicles` answers
"which of these cars is in the group's own stock" for the caller's token's
group only. It used to take a `group_id` and trust it, so any context could
have asked about another group's stock — a group read no test could see.
It now takes the caller's `Principal`, as `list_group_stock_items` authorises
its caller, and the group comes from the token.
"""

import inspect
import uuid

from app.core.auth import AccessRole, Principal
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.public import get_stock_items_for_vehicles
from app.inventory.schemas.stock_item import StockItemCreate
from app.platform.organisation.models.dealership import DealerGroup
from tests.stock_support import create_in_stock_item
from tests.test_sales_lifecycle_reservation import _dealership


def _principal(dealership) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), tenant_id=dealership.id, group_id=dealership.dealer_group_id,
        memberships=frozenset({dealership.id}), active_location_id=None, roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=False,
    )


def _in_stock(db_session, dealership, vin: str) -> uuid.UUID:
    item = create_in_stock_item(
        db_session, tenant_id=dealership.id,
        data=StockItemCreate(vehicle_label="Seat Leon", condition=StockItemCondition.USED, vin=vin),
        actor_id=None,
    )
    assert item.vehicle_id is not None
    return item.vehicle_id


def _group_read_on(db_session, dealership) -> None:
    db_session.get(DealerGroup, dealership.dealer_group_id).group_read_enabled = True
    db_session.commit()


def test_the_group_comes_from_the_callers_principal_never_from_an_id_it_passes():
    parameters = inspect.signature(get_stock_items_for_vehicles).parameters
    assert "group_id" not in parameters
    assert parameters["principal"].annotation in (Principal, "Principal")


def test_a_caller_sees_its_own_groups_stock_and_never_another_groups(db_session):
    ours, theirs = _dealership(db_session), _dealership(db_session)
    _group_read_on(db_session, ours)
    _group_read_on(db_session, theirs)
    our_car = _in_stock(db_session, ours, "WVWZZZ1KZAW538001")
    their_car = _in_stock(db_session, theirs, "WVWZZZ1KZAW538002")

    found = get_stock_items_for_vehicles(db_session, principal=_principal(ours), vehicle_ids=[our_car, their_car])

    assert set(found) == {our_car}
    assert set(get_stock_items_for_vehicles(db_session, principal=_principal(theirs), vehicle_ids=[our_car, their_car])) == {
        their_car
    }


def test_nothing_while_the_callers_group_read_is_off(db_session):
    ours = _dealership(db_session)
    car = _in_stock(db_session, ours, "WVWZZZ1KZAW538003")

    assert get_stock_items_for_vehicles(db_session, principal=_principal(ours), vehicle_ids=[car]) == {}

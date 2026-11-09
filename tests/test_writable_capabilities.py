"""KAN-430 (Anto, 2026-10-30, option A): an action a user's role may never
use is hidden, not offered and then refused. The SPA learns which rows of the
permission matrix the signed-in user may write from the session read
(`writableCapabilities` on login, `/auth/me` and the dealership switch),
computed from `CAPABILITY_MATRIX` — never a second role table in the
frontend. `may_write` is the one rule; `require_write` enforces it."""

import re
import uuid
from pathlib import Path

import pytest

from app.core.auth import AccessRole, Principal
from app.core.errors import ForbiddenError
from app.core.permissions import CAPABILITY_MATRIX, may_write, require_write, writable_capabilities
from tests.identity_support import PasswordLogin, cookie_headers, create_dealership, create_user, platform_admin_headers

_STOCK_ROWS = ("stock_items", "stock_acquisition", "stock_condition", "marketplace_publishing", "stock_reservation")


@pytest.fixture
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def test_a_salesperson_writes_stock_items_but_not_the_purchase_or_the_publishing():
    """The case the ticket names: since KAN-376 a sales user creates stock
    items, and the stock detail offered them the purchase booking, the cost
    booking and the publishing, which their role may not use."""

    writable = writable_capabilities(roles=frozenset({AccessRole.SALES}), is_dealer_manager=False)

    assert "stock_items" in writable
    assert "stock_acquisition" not in writable
    assert "marketplace_publishing" not in writable


def test_inventory_writes_the_purchase_and_the_publishing():
    writable = writable_capabilities(roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False)

    assert {"stock_items", "stock_acquisition", "marketplace_publishing"} <= set(writable)


def test_the_manager_writes_every_row_but_the_nobody_cells_and_platform_staff_every_row():
    manager = writable_capabilities(roles=frozenset(), is_dealer_manager=True)
    staff = writable_capabilities(roles=frozenset({AccessRole.PLATFORM_ADMIN}), is_dealer_manager=False)

    assert set(manager) == {name for name, cap in CAPABILITY_MATRIX.items() if cap.manager_can_write or cap.write_roles}
    assert staff == sorted(CAPABILITY_MATRIX)


def test_an_auditor_writes_nothing():
    assert writable_capabilities(roles=frozenset({AccessRole.AUDITOR}), is_dealer_manager=False) == []


@pytest.mark.parametrize("role", [r for r in AccessRole if r is not AccessRole.PLATFORM_ADMIN])
@pytest.mark.parametrize("manager", [False, True])
def test_the_list_is_exactly_the_rows_require_write_lets_through(role, manager):
    roles = frozenset({role})

    expected = sorted(
        name
        for name, cap in CAPABILITY_MATRIX.items()
        if roles & cap.write_roles or (cap.manager_can_write and manager)
    )

    assert writable_capabilities(roles=roles, is_dealer_manager=manager) == expected
    assert all(may_write(name, roles=roles, is_dealer_manager=manager) for name in expected)


def test_the_session_read_carries_the_users_writable_rows(client, login):
    dealership_id = create_dealership(client)
    salesperson = create_user(client, dealership_id, accessRoles=["sales"], isDealerManager=False)

    signed_in = login.login(salesperson)
    me = client.get("/v1/auth/me", headers=cookie_headers(login.session_cookie(salesperson)))

    assert signed_in.status_code == 200, signed_in.text
    assert me.status_code == 200, me.text
    expected = writable_capabilities(roles=frozenset({AccessRole.SALES}), is_dealer_manager=False)
    assert signed_in.json()["session"]["writableCapabilities"] == expected
    assert me.json()["writableCapabilities"] == expected
    stock = {row for row in me.json()["writableCapabilities"] if row in _STOCK_ROWS}
    assert stock == {"stock_items", "stock_condition"}


def test_the_managers_session_read_follows_the_manager_flag_of_the_active_dealership(client, login):
    dealership_id = create_dealership(client)
    manager = create_user(client, dealership_id, accessRoles=["sales"], isDealerManager=True)

    me = client.get("/v1/auth/me", headers=cookie_headers(login.session_cookie(manager)))

    assert me.status_code == 200, me.text
    assert {"stock_acquisition", "marketplace_publishing"} <= set(me.json()["writableCapabilities"])


def test_every_capability_the_spa_asks_about_is_a_matrix_row():
    """The SPA names the rows a screen asks about (`useCanWrite`) and nothing
    else; a name that is not a row would hide an action for everyone."""

    source = (Path(__file__).parent.parent / "frontend/apps/dms/src/auth/gatedCapabilities.ts").read_text()
    names = re.findall(r"'([a-z_]+)'", source.split("GATED_CAPABILITIES = [", 1)[1].split("]", 1)[0])

    assert names
    assert set(names) <= set(CAPABILITY_MATRIX)


@pytest.mark.parametrize("role", list(AccessRole))
@pytest.mark.parametrize("manager", [False, True])
def test_require_write_lets_through_exactly_the_listed_rows(role, manager):
    """The list and the gate are one rule: the dependency `require_write`
    builds passes for every listed row and refuses every other — for every
    role, platform staff included (KAN-511)."""

    tenant_id = uuid.uuid4()
    principal = Principal(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(), memberships=frozenset({tenant_id}),
        active_location_id=None, roles=frozenset({role}), is_dealer_manager=manager,
    )
    writable = set(writable_capabilities(roles=principal.roles, is_dealer_manager=manager))

    for name in CAPABILITY_MATRIX:
        check = require_write(name)
        if name in writable:
            assert check(principal) is principal
        else:
            with pytest.raises(ForbiddenError):
                check(principal)


def test_switching_to_a_sister_betrieb_follows_the_manager_flag_held_there(client, login):
    """A manager at home who is a plain member of a sister Betrieb writes
    there what the role allows, no more."""

    home = create_dealership(client)
    sister = create_dealership(client)
    manager = create_user(client, home, accessRoles=["sales"], isDealerManager=True)
    granted = client.post(
        f"/v1/dealerships/{home}/users/{manager['id']}/memberships",
        json={"dealershipId": sister, "isDealerManager": False},
        headers=platform_admin_headers(),
    )
    assert granted.status_code == 201, granted.text

    cookie = login.session_cookie(manager)
    switched = client.post("/v1/auth/switch-dealership", json={"dealershipId": sister}, headers=cookie_headers(cookie))

    assert switched.status_code == 200, switched.text
    assert switched.json()["writableCapabilities"] == writable_capabilities(
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False
    )
    assert "stock_acquisition" not in switched.json()["writableCapabilities"]


# KAN-211: the session's own manager flag — the ACTIVE Betrieb's, as the
# token carries it — on the session read, beside the capabilities. The SPA
# gates «Neu laden» on it; `user.isDealerManager` is the home flag and must
# not be used for that (platform.md).


def test_the_session_read_carries_the_active_betriebs_manager_flag(client, login):
    dealership_id = create_dealership(client)
    manager = create_user(client, dealership_id, accessRoles=["sales"], isDealerManager=True)
    salesperson = create_user(client, dealership_id, accessRoles=["sales"], isDealerManager=False)

    assert client.get("/v1/auth/me", headers=cookie_headers(login.session_cookie(manager))).json()["isActiveDealershipManager"] is True
    assert client.get("/v1/auth/me", headers=cookie_headers(login.session_cookie(salesperson))).json()["isActiveDealershipManager"] is False


def test_switching_to_a_sister_betrieb_reports_the_manager_flag_held_there(client, login):
    """A home manager switched into a sister Betrieb they do not manage: the
    home flag stays true on `user`, the session's own flag reads false."""

    home = create_dealership(client)
    sister = create_dealership(client)
    manager = create_user(client, home, accessRoles=["sales"], isDealerManager=True)
    granted = client.post(
        f"/v1/dealerships/{home}/users/{manager['id']}/memberships",
        json={"dealershipId": sister, "isDealerManager": False},
        headers=platform_admin_headers(),
    )
    assert granted.status_code == 201, granted.text

    cookie = login.session_cookie(manager)
    switched = client.post("/v1/auth/switch-dealership", json={"dealershipId": sister}, headers=cookie_headers(cookie))

    assert switched.status_code == 200, switched.text
    assert switched.json()["user"]["isDealerManager"] is True
    assert switched.json()["isActiveDealershipManager"] is False

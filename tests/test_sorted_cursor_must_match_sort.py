"""KAN-182: a sorted cursor replayed under a sort it was not minted for is a
400, never a 500.

`app.core.pagination.decode_sort_cursor` (preserved) checks only the
cursor's shape. Every sorted list now decodes through
`app.core.sorting.decode_cursor_for_sort`, which also checks the cursor
against the sort the request pages by: one value per sort field plus the row
id, each value of the type its column binds, an enum value a member of the
column's enum. Each failure mode is asserted on every sorted list endpoint
family, with the taxonomy's body and `details.reason` `cursor_invalid` (the
SPA's apiRefusal table already translates it).
"""

import base64
import datetime as dt
import json
import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.errors import BadRequestError
from app.core.pagination import SortCursorPosition, encode_sort_cursor
from app.core.sorting import SortField, cursor_column_supported, decode_cursor_for_sort, parse_sort
from app.platform.organisation.models.dealership import DealerGroup
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleStatus
from tests.identity_support import platform_admin_headers
from tests.token_support import dealer_group_of

REFUSAL = {
    "error": {
        "code": "bad_request",
        "message": "Cursor is malformed or invalid.",
        "details": {"reason": "cursor_invalid"},
    }
}


def _raw_cursor(payload: object) -> str:
    """A cursor as the wire carries it, built by hand: what a stale or
    hand-edited request sends."""

    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _row_id() -> list:
    return ["uuid", str(uuid.uuid4())]


def _manager_headers(tenant_id: uuid.UUID | None = None) -> dict[str, str]:
    tid = tenant_id or uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tid,
        group_id=dealer_group_of(str(tid)),
        roles=frozenset(),
        is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _users_case(_db) -> tuple[str, dict[str, str]]:
    tid = uuid.uuid4()
    return f"/v1/dealerships/{tid}/users", _manager_headers(tid)


def _group_stock_case(db) -> tuple[str, dict[str, str]]:
    """The group read is on: otherwise the list is a 404 whatever the cursor."""

    group = DealerGroup(name=f"KAN-182 {uuid.uuid4()}", group_read_enabled=True)
    db.add(group)
    db.commit()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group.id, roles=frozenset({AccessRole.INVENTORY})
    )
    return "/v1/inventory/groups/mine/stock-items", {"Authorization": f"Bearer {token}"}


# Every sorted list endpoint family: (path and headers, query params, a value
# of the one sort field each case pages by). No `sort` param is a list with a
# fixed one-field order (notifications by delivery, dealer groups by name).
_WHEN = ["dt", "2026-01-01T00:00:00+00:00"]
ENDPOINTS = {
    "customers": (lambda _db: ("/v1/customers", _manager_headers()), {"sort": "lastName:asc"}, ["str", "Muster"]),
    "offers": (lambda _db: ("/v1/sales/offers", _manager_headers()), {"sort": "updatedAt:desc"}, _WHEN),
    "contracts": (lambda _db: ("/v1/sales/contracts", _manager_headers()), {"sort": "updatedAt:desc"}, _WHEN),
    "deals": (lambda _db: ("/v1/sales/deals", _manager_headers()), {"sort": "updatedAt:desc"}, _WHEN),
    "valuations": (lambda _db: ("/v1/valuations", _manager_headers()), {"sort": "createdAt:desc"}, _WHEN),
    "stock items": (lambda _db: ("/v1/inventory/stock-items", _manager_headers()), {"sort": "updatedAt:desc"}, _WHEN),
    "group stock": (_group_stock_case, {"sort": "updatedAt:desc"}, _WHEN),
    "vehicle-mdm search": (
        lambda _db: ("/v1/vehicle-mdm/search", _manager_headers()),
        {"sort": "vin:asc"},
        ["str", "W"],
    ),
    "integration connections": (
        lambda _db: ("/v1/integrations/connections", _manager_headers()),
        {"sort": "updatedAt:desc"},
        _WHEN,
    ),
    "dealership users": (_users_case, {"sort": "lastName:asc"}, ["str", "Muster"]),
    "platform user search": (
        lambda _db: ("/v1/platform/users", platform_admin_headers()),
        {"email": "nobody@example.ch", "sort": "lastName:asc"},
        ["str", "Muster"],
    ),
    "dealer groups": (lambda _db: ("/v1/platform/dealer-groups", platform_admin_headers()), {}, ["str", "Gruppe"]),
    "notifications inbox": (lambda _db: ("/v1/me/notifications", _manager_headers()), {}, _WHEN),
}

# Every failure mode the ticket names, as a cursor for a one-field sort, built
# from a value that fits the field.
FAILURE_MODES = {
    # Count mismatch: two values for one sort field, or none at all.
    "more values than sort fields": lambda fits: [fits, fits, _row_id()],
    "no value for the sort field": lambda fits: [_row_id()],
    # Type mismatch: a boolean binds into none of the columns sorted on here.
    "value of another type": lambda fits: [["bool", True], _row_id()],
    # The row id is always a UUID.
    "row id not a uuid": lambda fits: [fits, ["int", 7]],
}


def _get(client, db, endpoint: str, cursor: str, **extra):
    path_and_headers, params, _fits = ENDPOINTS[endpoint]
    path, headers = path_and_headers(db)
    return client.get(path, params={**params, **extra, "cursor": cursor}, headers=headers)


@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
def test_a_cursor_that_fits_the_sort_is_accepted(client, db_session, endpoint):
    """The control: each case's headers and params reach the list, and a
    cursor of the right shape pages (here past every row)."""

    response = _get(client, db_session, endpoint, _raw_cursor([ENDPOINTS[endpoint][2], _row_id()]))

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("mode", [*FAILURE_MODES, "malformed"])
@pytest.mark.parametrize("endpoint", list(ENDPOINTS))
def test_a_cursor_that_does_not_fit_the_sort_is_a_400_on_every_sorted_list(client, db_session, endpoint, mode):
    # «malformed»: what the preserved decoder already refused keeps its 400, now with the reason.
    cursor = "not-a-cursor" if mode == "malformed" else _raw_cursor(FAILURE_MODES[mode](ENDPOINTS[endpoint][2]))

    response = _get(client, db_session, endpoint, cursor)

    assert response.status_code == 400, response.text
    assert response.json() == REFUSAL


# The enum failure mode: the endpoints with a StoredEnum column among their sort fields.
ENUM_SORTS = {
    "vehicle-mdm search": "vehicleStatus:asc",
    "dealership users": "status:asc",
    "platform user search": "status:asc",
}


@pytest.mark.parametrize("endpoint", list(ENUM_SORTS))
def test_an_enum_value_that_is_no_member_is_a_400(client, db_session, endpoint):
    response = _get(
        client, db_session, endpoint, _raw_cursor([["str", "not_a_member"], _row_id()]), sort=ENUM_SORTS[endpoint]
    )

    assert response.status_code == 400, response.text
    assert response.json() == REFUSAL


def test_the_tickets_reproduction_a_default_cursor_replayed_under_an_enum_sort(client, db_session):
    """The ticket's first case: page 1 of the vehicle list in its default
    vehicle-number order, its next cursor sent with `sort=vehicleStatus:asc`.
    The vehicle number reached the StoredEnum bind and raised LookupError (500)."""

    for n, status in enumerate((VehicleStatus.ACTIVE, VehicleStatus.STOLEN), start=1):
        db_session.add(
            VehicleMdm(
                vehicle_number=f"K182-{n:04d}",
                vin=f"VINKANONEEIGHTY{n:02d}",
                vehicle_status=status,
                catalogue_match_status=CatalogueMatchStatus.UNVERIFIED,
            )
        )
    db_session.commit()
    headers = _manager_headers()

    first = client.get("/v1/vehicle-mdm/search", params={"q": "K182", "limit": 1}, headers=headers)
    assert first.status_code == 200, first.text
    cursor = first.json()["filtered"]["nextCursor"]
    assert cursor

    replayed = client.get(
        "/v1/vehicle-mdm/search",
        params={"q": "K182", "limit": 1, "sort": "vehicleStatus:asc", "cursor": cursor},
        headers=headers,
    )
    assert replayed.status_code == 400, replayed.text
    assert replayed.json() == REFUSAL

    # The same cursor under the sort it was minted for still pages on.
    second = client.get("/v1/vehicle-mdm/search", params={"q": "K182", "limit": 1, "cursor": cursor}, headers=headers)
    assert second.status_code == 200, second.text
    assert [v["vehicleNumber"] for v in second.json()["filtered"]["items"]] == ["K182-0002"]


def test_the_tickets_second_case_an_int_cursor_under_a_string_sort(client, db_session):
    """A crafted int cursor with `sort=vin:asc` reached psycopg as
    `varchar > integer` — `operator does not exist`, a 500."""

    response = _get(client, db_session, "vehicle-mdm search", _raw_cursor([["int", 5], _row_id()]))

    assert response.status_code == 400, response.text
    assert response.json() == REFUSAL


# --- the decoder itself -------------------------------------------------------

_STATUS = parse_sort(
    "vehicleStatus:asc,stammnummer:desc",
    allowed={
        "vehicleStatus": VehicleMdm.vehicle_status,
        "stammnummer": VehicleMdm.stammnummer,
    },
)


def _decodes(payload: list) -> bool:
    try:
        decode_cursor_for_sort(_raw_cursor(payload), _STATUS)
    except BadRequestError as exc:
        assert exc.details == {"reason": "cursor_invalid"}
        return False
    return True


def test_a_cursor_minted_for_the_sort_decodes():
    cursor = encode_sort_cursor(SortCursorPosition(values=("active", "123456789"), id=uuid.uuid4()))
    position = decode_cursor_for_sort(cursor, _STATUS)
    assert position is not None and position.values == ("active", "123456789")
    assert decode_cursor_for_sort(None, _STATUS) is None
    assert decode_cursor_for_sort("", _STATUS) is None


def test_null_is_accepted_only_on_a_nullable_column():
    assert _decodes([["str", "active"], ["null", None], _row_id()])  # stammnummer is nullable
    assert not _decodes([["null", None], ["str", "123456789"], _row_id()])  # vehicle_status is not


def test_each_type_mismatch_is_refused():
    assert not _decodes([["str", "active"], ["int", 123456789], _row_id()])
    assert not _decodes([["str", "active"], ["dt", "2026-01-01T00:00:00+00:00"], _row_id()])
    assert not _decodes([["str", "active"], ["uuid", str(uuid.uuid4())], _row_id()])
    assert not _decodes([["str", "nope"], ["str", "123456789"], _row_id()])
    assert not _decodes([["str", "active"], ["str", "12\u00003"], _row_id()])  # NUL: Postgres text cannot hold it


def test_each_column_type_takes_its_own_tag_only():
    from app.platform.models.user import User
    from app.sales.models.deal import SalesDeal

    def fits(column, tagged) -> bool:
        field = [SortField(api_name="x", column=column, direction="asc", nullable=False)]
        try:
            decode_cursor_for_sort(_raw_cursor([tagged, _row_id()]), field)
        except BadRequestError:
            return False
        return True

    when = ["dt", dt.datetime(2026, 1, 1, tzinfo=dt.UTC).isoformat()]
    assert fits(SalesDeal.updated_at, when) and not fits(SalesDeal.updated_at, ["str", "2026-01-01"])
    assert fits(SalesDeal.gross_price, ["int", 100]) and not fits(SalesDeal.gross_price, ["str", "100"])
    assert not fits(SalesDeal.gross_price, ["bool", True])
    assert fits(User.valid_from, when) and not fits(User.valid_from, ["int", 1])


def test_every_sortable_column_is_one_a_cursor_can_be_checked_against():
    """A column of a type `decode_cursor_for_sort` cannot judge would refuse
    every cursor on its sort — a sortable column must be of a supported type."""

    from app.customer.api.customers import CUSTOMER_SORT_FIELDS
    from app.integration.api.connections import CONNECTION_SORT_FIELDS
    from app.inventory.api.stock_items import STOCK_ITEM_SORT_FIELDS
    from app.platform.api.dealership_users import USER_SORT_FIELDS
    from app.platform.api.recovery import RECOVERY_SORT_FIELDS
    from app.sales.api.contracts import CONTRACT_SORT_FIELDS
    from app.sales.api.deals import DEAL_SORT_FIELDS
    from app.sales.api.offers import OFFER_SORT_FIELDS
    from app.valuation.api.valuations import VALUATION_SORT_FIELDS
    from app.vehicle.api.vehicle_mdm import VEHICLE_MDM_SORT_FIELDS

    allow_lists = (
        CUSTOMER_SORT_FIELDS,
        CONNECTION_SORT_FIELDS,
        STOCK_ITEM_SORT_FIELDS,
        USER_SORT_FIELDS,
        RECOVERY_SORT_FIELDS,
        CONTRACT_SORT_FIELDS,
        DEAL_SORT_FIELDS,
        OFFER_SORT_FIELDS,
        VALUATION_SORT_FIELDS,
        VEHICLE_MDM_SORT_FIELDS,
    )
    unsupported = [
        f"{column}" for allowed in allow_lists for column in allowed.values() if not cursor_column_supported(column)
    ]
    assert unsupported == []


def test_every_fixed_sort_declares_its_columns_nullability_truly():
    """A hand-built SortField (a default sort, a fixed order) states
    `nullable` itself; the decoder refuses NULL on a field that says it is
    not nullable, so a list that can hold NULL there would refuse its own
    next cursor. The statement matches the column, unless the list's query
    excludes NULL — named below with where."""

    # api name -> where the list excludes NULL
    excludes_null = {"deliveredAt": "app/platform/services/notifications.py::_inbox (delivered_at IS NOT NULL)"}

    from app.customer.api.customers import _DEFAULT_CUSTOMER_SORT
    from app.integration.api.connections import _DEFAULT_CONNECTION_SORT
    from app.inventory.api.group import _DEFAULT_GROUP_STOCK_SORT
    from app.inventory.api.stock_items import _DEFAULT_STOCK_ITEM_SORT
    from app.platform.api.dealership_users import _DEFAULT_USER_SORT
    from app.platform.api.notifications import _INBOX_ORDER
    from app.platform.api.recovery import _DEFAULT_SORT
    from app.platform.organisation.services.dealer_group_options import _BY_NAME
    from app.sales.api.contracts import _DEFAULT_CONTRACT_SORT
    from app.sales.api.deals import _DEFAULT_DEAL_SORT
    from app.sales.api.offers import _DEFAULT_OFFER_SORT
    from app.valuation.api.valuations import _DEFAULT_VALUATION_SORT
    from app.vehicle.api.vehicle_mdm import _DEFAULT_VEHICLE_MDM_SORT

    fixed = [
        *_DEFAULT_CUSTOMER_SORT,
        *_DEFAULT_CONNECTION_SORT,
        *_DEFAULT_GROUP_STOCK_SORT,
        *_DEFAULT_STOCK_ITEM_SORT,
        *_DEFAULT_USER_SORT,
        *_INBOX_ORDER,
        *_DEFAULT_SORT,
        *_BY_NAME,
        *_DEFAULT_CONTRACT_SORT,
        *_DEFAULT_DEAL_SORT,
        *_DEFAULT_OFFER_SORT,
        *_DEFAULT_VALUATION_SORT,
        *_DEFAULT_VEHICLE_MDM_SORT,
    ]
    wrong = [
        f.api_name
        for f in fixed
        if f.nullable != f.column.property.columns[0].nullable and f.api_name not in excludes_null
    ]
    assert wrong == []
    assert all(cursor_column_supported(f.column) for f in fixed)

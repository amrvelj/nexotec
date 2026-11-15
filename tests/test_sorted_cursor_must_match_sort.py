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
    from app.platform.identity.models.user import User
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
    every cursor on its sort — a sortable column must be of a supported type.
    The allow-lists are `_all_sort_allow_lists()`, shared with KAN-713's minting
    guard below, so a new sorted list is named once."""

    unsupported = [
        f"{column}"
        for allowed in _all_sort_allow_lists().values()
        for column in allowed.values()
        if not cursor_column_supported(column)
    ]
    assert unsupported == []

    # KAN-461: a list-query registry's sort columns too (the layer carries a
    # decimal, a date and a boolean through the preserved cursor format).
    from app.inventory.services.stock_grid import STOCK_ITEM_COLUMNS

    registries = (STOCK_ITEM_COLUMNS,)
    unsupported = [
        spec.api_name
        for registry in registries
        for spec in registry
        if not cursor_column_supported(spec.sort_column if spec.sort_column is not None else spec.column)
    ]
    assert unsupported == []


def test_every_fixed_sort_declares_its_columns_nullability_truly():
    """A hand-built SortField (a default sort, a fixed order) states
    `nullable` itself; the decoder refuses NULL on a field that says it is
    not nullable, so a list that can hold NULL there would refuse its own
    next cursor. The statement matches the column, unless the list's query
    excludes NULL — named below with where."""

    # api name -> where the list excludes NULL
    excludes_null = {"deliveredAt": "app/platform/identity/services/notifications.py::_inbox (delivered_at IS NOT NULL)"}

    from app.customer.api.customers import _DEFAULT_CUSTOMER_SORT
    from app.integration.api.connections import _DEFAULT_CONNECTION_SORT
    from app.inventory.api.group import _DEFAULT_GROUP_STOCK_SORT
    from app.inventory.api.stock_items import _DEFAULT_STOCK_ITEM_SORT
    from app.platform.api.dealership_users import _DEFAULT_USER_SORT
    from app.platform.api.recovery import _DEFAULT_SORT
    from app.platform.identity.api.notifications import _INBOX_ORDER
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


# --- KAN-713: a date or money sort pages past page one --------------------------
#
# `app.core.pagination._serialize_sort_value` (preserved) writes no `date` and no
# `Decimal`, so the next cursor of a sort on a Date or Numeric column raised
# TypeError — the first page answered, the second was a 500. Every sorted list now
# mints its cursor through `app.core.sorting.build_page_for_sort`, which carries a
# Numeric value as its scaled integer and a date as a midnight datetime, on the
# existing tags, and `decode_cursor_for_sort` turns both back.


def _sample_value(column):
    """A value of the type `column` holds, as a row read from the database carries it."""

    import decimal

    from app.core.enum_type import StoredEnum
    from app.core.sorting import _bind_type, _cursor_python_type
    from app.core.types import GUID

    expected = _cursor_python_type(column)
    if isinstance(expected, StoredEnum):
        return min(expected.values)
    if isinstance(expected, GUID) or expected is uuid.UUID:
        return uuid.uuid4()
    if expected is bool:
        return True
    if expected is int:
        return 7
    if expected is decimal.Decimal:
        scale = _bind_type(column.property.columns[0].type).scale
        return decimal.Decimal("1234.5").quantize(decimal.Decimal(1).scaleb(-scale))
    if expected is dt.datetime:
        return dt.datetime(2026, 3, 1, 9, 30, tzinfo=dt.UTC)
    if expected is dt.date:
        return dt.date(2026, 3, 1)
    if expected is str:
        return "Muster"
    raise AssertionError(f"no sample for {column}")


def _round_trip(column, value):
    """The value a cursor minted on a row holding `value` decodes back to, under a sort on `column`."""

    from types import SimpleNamespace

    from app.core.pagination import SortPageParams
    from app.core.sorting import build_page_for_sort

    field = SortField(api_name="x", column=column, direction="asc", nullable=column.property.columns[0].nullable)
    key = column.key
    rows = [SimpleNamespace(**{key: value, "id": uuid.uuid4()}) for _ in range(2)]
    page, cursor = build_page_for_sort(rows, SortPageParams(limit=1, cursor=None, sort_fields=[field]))
    assert page == rows[:1] and cursor
    position = decode_cursor_for_sort(cursor, [field])
    assert position is not None and position.id == rows[0].id
    from sqlalchemy.sql.elements import BindParameter

    back = position.values[0]
    # A boolean comes back bound (`column > False` is refused unbound); judge the value it binds.
    return back.value if isinstance(back, BindParameter) else back


def _all_sort_allow_lists() -> dict[str, dict]:
    from app.customer.api.customers import CUSTOMER_SORT_FIELDS
    from app.integration.api.connections import CONNECTION_SORT_FIELDS
    from app.inventory.api.group import GROUP_STOCK_SORT_FIELDS
    from app.inventory.services.stock_grid import STOCK_ITEM_COLUMNS
    from app.platform.api.dealership_users import USER_SORT_FIELDS
    from app.platform.api.recovery import RECOVERY_SORT_FIELDS
    from app.sales.api.contracts import CONTRACT_SORT_FIELDS
    from app.sales.api.deals import DEAL_SORT_FIELDS
    from app.sales.api.offers import OFFER_SORT_FIELDS
    from app.valuation.api.valuations import VALUATION_SORT_FIELDS
    from app.vehicle.api.vehicle_mdm import VEHICLE_MDM_SORT_FIELDS

    return {
        "customers": CUSTOMER_SORT_FIELDS,
        "connections": CONNECTION_SORT_FIELDS,
        "group stock": GROUP_STOCK_SORT_FIELDS,
        "dealership users": USER_SORT_FIELDS,
        "recovery": RECOVERY_SORT_FIELDS,
        "contracts": CONTRACT_SORT_FIELDS,
        "deals": DEAL_SORT_FIELDS,
        "offers": OFFER_SORT_FIELDS,
        "valuations": VALUATION_SORT_FIELDS,
        "vehicle-mdm": VEHICLE_MDM_SORT_FIELDS,
        # A grid on the list-query layer (ADR-179) sorts by its registry's sortable columns.
        "stock grid (list-query registry)": {
            spec.api_name: spec.sort_column if spec.sort_column is not None else spec.column
            for spec in STOCK_ITEM_COLUMNS
            if spec.sortable
        },
    }


def test_every_sort_allow_list_column_mints_a_cursor_that_decodes_to_its_own_value():
    """The decode-side guard above checks that a cursor *could* be judged
    against each column; this one checks that a cursor can be *minted* from a
    row on it at all, and decodes back to the row's value of the row's type —
    what KAN-713's four sorts failed."""

    failures = []
    for name, allowed in _all_sort_allow_lists().items():
        for api_name, column in allowed.items():
            value = _sample_value(column)
            try:
                back = _round_trip(column, value)
            except Exception as exc:  # noqa: BLE001 — every failure is reported, not just the first
                failures.append(f"{name}.{api_name}: {type(exc).__name__}: {exc}")
                continue
            if back != value or type(back) is not type(value):
                failures.append(f"{name}.{api_name}: {value!r} came back as {back!r}")
    assert failures == []


def test_a_null_on_a_nullable_date_or_money_column_round_trips():
    from app.platform.identity.models.user import User
    from app.sales.models.deal import SalesDeal

    assert _round_trip(User.valid_to, None) is None
    assert _round_trip(SalesDeal.gross_price, None) is None


def test_a_money_cursor_keeps_every_digit_of_the_column():
    import decimal

    from app.sales.models.deal import SalesDeal

    for raw in ("0.00", "0.01", "-12.50", "9999999999.99"):
        value = decimal.Decimal(raw)
        assert _round_trip(SalesDeal.gross_price, value) == value
    # 12.505 has more digits than the scale; NaN fits a numeric(12, 2) column but no path of the app writes it.
    for unrepresentable in ("12.505", "NaN", "Infinity"):
        with pytest.raises(TypeError):
            _round_trip(SalesDeal.gross_price, decimal.Decimal(unrepresentable))


def test_a_date_cursor_that_is_not_midnight_is_refused():
    """The encoder writes a date as midnight; any other time is a cursor it
    never minted, and comparing a date column against it would page by a
    different day boundary."""

    from app.platform.identity.models.user import User

    field = [SortField(api_name="validFrom", column=User.valid_from, direction="asc", nullable=True)]
    with pytest.raises(BadRequestError):
        decode_cursor_for_sort(_raw_cursor([["dt", "2026-03-01T09:30:00"], _row_id()]), field)


def _walk(client, path: str, *, params: dict, headers: dict, key: str = "items") -> list[dict]:
    """Every row of a sorted list, a page of two at a time; each page must answer 200."""

    seen: list[dict] = []
    cursor = None
    for _ in range(20):
        response = client.get(
            path, params={**params, "limit": 2, **({"cursor": cursor} if cursor else {})}, headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        seen += body[key]
        cursor = body["nextCursor"]
        if not cursor:
            return seen
    raise AssertionError("the list never ended")


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_valuations_sorted_by_final_offer_page_past_page_one(client, db_session, direction):
    from tests.vin_support import unique_vin

    tid = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=dealer_group_of(str(tid)), roles=frozenset({AccessRole.SALES})
    )
    headers = {"Authorization": f"Bearer {token}"}
    offers = ["1500.50", "200.25", "99999.99", "200.25", "0.00"]
    ids = []
    for offer in offers:
        created = client.post(
            "/v1/valuations",
            json={
                "vin": unique_vin(),
                "vehicleMake": "VW",
                "vehicleModel": "Golf",
                "finalOffer": offer,
                "source": "manual",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])

    rows = _walk(client, "/v1/valuations", params={"sort": f"finalOffer:{direction}"}, headers=headers)

    from decimal import Decimal

    got = [Decimal(r["finalOffer"]) for r in rows]
    assert got == sorted((Decimal(o) for o in offers), reverse=direction == "desc")
    assert sorted(r["id"] for r in rows) == sorted(ids)  # every row once: none skipped, none repeated


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_deals_sorted_by_gross_price_page_past_page_one(client, db_session, direction):
    from decimal import Decimal

    from app.sales.models.deal import SalesDeal

    tid = uuid.uuid4()
    # Four prices and three NULLs: with pages of two, the third page ends on a
    # NULL row, so the fourth page is asked for with a cursor inside the NULL tail.
    prices = [Decimal("45000.00"), None, Decimal("12500.50"), Decimal("12500.50"), Decimal("0.00"), None, None]
    for n, price in enumerate(prices):
        db_session.add(
            SalesDeal(tenant_id=tid, entity_type="offer", number=f"O-713-{n}", status="open", gross_price=price)
        )
    db_session.commit()

    rows = _walk(client, "/v1/sales/deals", params={"sort": f"grossPrice:{direction}"}, headers=_manager_headers(tid))

    got = [None if r["grossPrice"] is None else Decimal(r["grossPrice"]) for r in rows]
    priced = sorted((p for p in prices if p is not None), reverse=direction == "desc")
    assert got == [*priced, None, None, None]  # nulls last in both directions (FR-UI-01)
    assert len({r["id"] for r in rows}) == len(prices)


@pytest.mark.parametrize("field", ["validFrom", "validTo"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_dealership_users_sorted_by_a_date_page_past_page_one(client, db_session, field, direction):
    from app.platform.identity.models.user import User
    from tests.identity_support import create_dealership, create_user

    dealership = create_dealership(client)
    days = [dt.date(2026, 5, 1), None, dt.date(2025, 1, 31), dt.date(2026, 5, 1), dt.date(2027, 12, 31)]
    column = "valid_from" if field == "validFrom" else "valid_to"
    ids = []
    for n, day in enumerate(days):
        user = create_user(client, dealership, lastName=f"Datum{n}")
        row = db_session.get(User, uuid.UUID(user["id"]))
        setattr(row, column, day)
        ids.append(user["id"])
    db_session.commit()

    rows = _walk(
        client,
        f"/v1/dealerships/{dealership}/users",
        params={"sort": f"{field}:{direction}"},
        headers=platform_admin_headers(),
    )

    got = [r[field] for r in rows]
    dated = sorted((d.isoformat() for d in days if d is not None), reverse=direction == "desc")
    assert got == [*dated, None]
    assert sorted(r["id"] for r in rows) == sorted(ids)


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_dealership_users_sorted_by_a_boolean_page_past_page_one(client, db_session, direction):
    """Found merging KAN-461 into KAN-713: a decoded boolean reached the keyset
    as a Python constant, and SQLAlchemy refuses `column > False` — page two of
    a sort on a Boolean column was a 500. `decode_cursor_for_sort` now binds it."""

    from tests.identity_support import create_dealership, create_user

    dealership = create_dealership(client)
    flags = [True, False, True, False, False]
    ids = [create_user(client, dealership, lastName=f"Flag{n}", isDealerManager=flag)["id"] for n, flag in enumerate(flags)]

    rows = _walk(
        client,
        f"/v1/dealerships/{dealership}/users",
        params={"sort": f"isDealerManager:{direction}"},
        headers=platform_admin_headers(),
    )

    assert [r["isDealerManager"] for r in rows] == sorted(flags, reverse=direction == "desc")
    assert sorted(r["id"] for r in rows) == sorted(ids)

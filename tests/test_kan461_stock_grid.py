"""KAN-461 — every Stock grid column sortable and filterable, from one served
registry (FR-UI-01, ADR-058, ADR-060; U-10 closed by Anto, 2026-10-26).

The tests that would have caught it (exit criterion 5): the registry holds
every persisted column or names why not; every field the schema serves sorts
both ways, paged; every condition it serves filters, and the rows are the
ones an independent evaluation of the predicate picks. Over HTTP, on
Postgres.
"""

import datetime as dt
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect

from app.core.auth import AccessRole
from app.core.base import utcnow
from app.core.list_query import OPERATORS_BY_TYPE
from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemRead
from app.inventory.services.stock_grid import STOCK_ITEM_COLUMNS, STOCK_ITEM_EXCLUDED_COLUMNS
from app.inventory.services.stock_item import compute_ageing_bucket
from tests.test_grid_exclusion_filters import _headers

ZURICH = ZoneInfo("Europe/Zurich")
SPECS = {spec.api_name: spec for spec in STOCK_ITEM_COLUMNS}
ROWS = 7
_WORDS = ["Alpha", "beta", "Gamma", "alpha", "Delta", "Epsilon", "Zeta"]
_DAY_OFFSETS = [0, None, -3, -40, -400, 10, -3]  # days from today; None = no value


# --- Exit criterion 5(a): the registry is complete ------------------------------------


def test_every_persisted_stock_column_is_a_grid_column_or_excluded_with_a_reason():
    persisted = {column.key for column in inspect(StockItem).columns}
    registered = {spec.column.key for spec in STOCK_ITEM_COLUMNS if spec.column is not None}
    excluded = set(STOCK_ITEM_EXCLUDED_COLUMNS)

    assert registered & excluded == set()
    assert persisted - registered - excluded == set(), "decide: a grid column, or excluded with its reason"
    assert (registered | excluded) - persisted == set()
    assert all(reason.strip() for reason in STOCK_ITEM_EXCLUDED_COLUMNS.values())


def test_every_grid_column_is_a_field_of_the_stock_items_read():
    read_fields = {field.alias or name for name, field in StockItemRead.model_fields.items()}
    assert set(SPECS) <= read_fields


def test_every_stock_grid_column_sorts():
    assert [spec.api_name for spec in STOCK_ITEM_COLUMNS if not spec.sortable] == []


# --- The served schema ------------------------------------------------------------------


def test_the_grid_schema_serves_the_registry(client):
    response = client.get("/v1/inventory/stock-items/grid-schema", headers=_headers(uuid.uuid4(), AccessRole.INVENTORY))

    assert response.status_code == 200, response.text
    fields = {field["field"]: field for field in response.json()["fields"]}
    assert set(fields) == set(SPECS)
    assert fields["listPrice"] == {
        "field": "listPrice", "type": "money", "operators": list(OPERATORS_BY_TYPE["money"]), "sortable": True,
        "labelKey": "stockList.columns.listPrice", "defaultVisible": False, "options": None,
    }
    assert fields["condition"]["options"] == [
        {"value": code.value, "labelKey": f"stockEnums.condition.{code.value}"} for code in StockItemCondition
    ]
    assert fields["updatedAt"]["operators"][-3:] == ["on", "before", "after"]
    assert {name for name, field in fields.items() if field["defaultVisible"]} == {
        "stockNumber", "vehicleLabel", "condition", "lifecycleStatus", "reservationState", "odometerKm",
        "effectivePrice", "updatedAt",
    }


# --- The seeded stock ------------------------------------------------------------------------


def _value(spec, i: int, now: dt.datetime):
    column = spec.column
    nullable = column.property.columns[0].nullable
    kind = spec.value_type
    if kind == "text":
        if spec.api_name == "stockNumber":
            return f"L-{1000 + (i * 37) % 97:04d}"
        if spec.api_name in ("vin", "tradeInVin"):
            return None if i in (1, 5) else f"WVWZZZ1KZAW{i:06d}"
        if nullable and i in (1, 5):
            return None
        return f"{spec.api_name} {_WORDS[i]}"
    if kind == "enum":
        members = list(spec.enum)
        return members[(i * 2) % len(members)]
    if kind in ("number", "money"):
        if nullable and i in (1, 5):
            return None
        if spec.api_name == "odometerKm":
            return [12000, None, 45000, 12000, 80000, None, 999][i]
        scale = Decimal("0.01")
        base = Decimal(["25900", "0", "18450.5", "25900", "99000", "0", "7.7"][i])
        if spec.api_name == "notionalInputTaxRate":
            base = Decimal(["8.1", "0", "7.7", "8.1", "2.5", "0", "3.7"][i])
        return base.quantize(scale)
    if kind == "boolean":
        values = [True, None, False, True, False, None, True]
        return values[i] if nullable else bool(values[i])
    offset = _DAY_OFFSETS[i]
    if offset is None:
        return None if nullable else now
    if kind == "date":
        return now.astimezone(ZURICH).date() + dt.timedelta(days=offset)
    return now + dt.timedelta(days=offset, hours=-i)


@pytest.fixture
def stock(db_session):
    """Seven cars of one Betrieb, every grid column filled with values that
    tie, differ in case, and are missing; another Betrieb's car beside."""

    tenant_id = uuid.uuid4()
    now = utcnow()
    ids = []
    for i in range(ROWS):
        item = StockItem(
            tenant_id=tenant_id, stock_number="", vehicle_label="", lifecycle_status=LifecycleStatus.PIPELINE,
            reservation_state=ReservationState.NONE, condition=StockItemCondition.USED,
        )
        for spec in STOCK_ITEM_COLUMNS:
            if spec.column is not None:
                setattr(item, spec.column.key, _value(spec, i, now))
        # The ageing bucket reads in_stock_at: green, amber, red and none.
        item.in_stock_at = [now - dt.timedelta(days=1), None, now - dt.timedelta(days=80),
                            now - dt.timedelta(days=200), now - dt.timedelta(days=61, hours=1), None,
                            now - dt.timedelta(days=60)][i]
        item.lifecycle_status = LifecycleStatus.IN_STOCK if item.in_stock_at else LifecycleStatus.PIPELINE
        db_session.add(item)
        db_session.flush()
        ids.append(item.id)
    other = StockItem(
        tenant_id=uuid.uuid4(), stock_number="L-9999", vehicle_label="Fremd", lifecycle_status=LifecycleStatus.PIPELINE,
        reservation_state=ReservationState.NONE, condition=StockItemCondition.USED,
    )
    db_session.add(other)
    db_session.commit()
    items = {item_id: db_session.get(StockItem, item_id) for item_id in ids}
    return tenant_id, items


def _key(spec, item):
    if spec.api_name == "ageingBucket":
        return item.in_stock_at
    return getattr(item, spec.column.key)


def _pages(client, headers, **params) -> list[str]:
    ids: list[str] = []
    cursor = None
    for _ in range(20):
        query = {**params, "limit": 3, **({"cursor": cursor} if cursor else {})}
        response = client.get("/v1/inventory/stock-items", params=query, headers=headers)
        assert response.status_code == 200, (params, response.text)
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        cursor = body["nextCursor"]
        if cursor is None:
            return ids
    raise AssertionError("paging never ended")


# --- Exit criterion 5(b): every field sorts, both ways, across pages ----------------------


@pytest.mark.parametrize("field", list(SPECS))
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_every_field_sorts_both_ways_across_pages(client, stock, field, direction):
    tenant_id, items = stock
    spec = SPECS[field]
    ids = _pages(client, _headers(tenant_id, AccessRole.INVENTORY), sort=f"{field}:{direction}")

    assert sorted(ids) == sorted(str(i) for i in items), "every car once, none of another Betrieb"
    keys = [_key(spec, items[uuid.UUID(i)]) for i in ids]
    present = [k for k in keys if k is not None]
    assert keys[: len(present)] == present, "the cars without a value come last"
    # The ageing bucket orders by days on the lot: green (most recent) first.
    descending = (direction == "desc") != spec.sort_inverted
    present = [k.value if hasattr(k, "value") and not isinstance(k, bool) else k for k in present]
    assert present == sorted(present, reverse=descending)


# --- Exit criterion 5(b): every condition filters, the rows are the right ones -------------


def _as_day(value):
    if isinstance(value, dt.datetime):
        return value.astimezone(ZURICH).date()
    return value


def _cases(spec, items) -> list[tuple[str, str | None, object]]:
    """(operator, wire value, the value the evaluator compares with) for every
    condition the field serves."""

    values = [_key(spec, item) for item in items.values()]
    present = [v for v in values if v is not None]
    kind = spec.value_type
    cases = []
    for op in spec.allowed_operators:
        if kind == "text":
            word = present[0]
            needle = word.split(" ")[-1][1:4] if op == "contains" else word.upper()
            cases.append((op, needle, needle))
        elif kind == "enum":
            codes = list(spec.enum_codes)
            cases.append((op, ",".join(codes[:2]) if op == "in" else codes[0], codes[:2] if op == "in" else codes[0]))
        elif kind in ("number", "money"):
            ordered = sorted(present)
            mid = ordered[len(ordered) // 2]
            if op == "between":
                cases.append((op, f"{ordered[0]},{mid}", (ordered[0], mid)))
            else:
                cases.append((op, str(mid), Decimal(str(mid))))
        elif kind == "boolean":
            cases.extend([(op, "true", True), (op, "false", False)])
        elif op in ("inTheLastDays", "moreThanDaysAgo"):
            cases.append((op, "30", 30))
        elif op in ("on", "before", "after"):
            day = _as_day(present[2])
            cases.append((op, day.isoformat(), day))
        else:
            cases.append((op, None, None))
    return cases


def _matches(spec, op, expected, item) -> bool:
    value = _key(spec, item)
    if spec.api_name == "ageingBucket":
        bucket = compute_ageing_bucket(item)
        value = bucket.value if bucket else None
    elif hasattr(value, "value") and spec.value_type == "enum":
        value = value.value
    kind = spec.value_type
    if kind == "text":
        if op == "contains":
            return value is not None and expected.lower() in value.lower()
        same = value is not None and value.lower() == expected.lower()
        return same if op == "equals" else not same
    if kind == "enum":
        if op == "in":
            return value in expected
        return value == expected if op == "is" else value != expected
    if kind in ("number", "money"):
        if value is None:
            return False
        if op == "between":
            low, high = expected
            return low <= value <= high
        return {"equals": value == expected, "greaterThan": value > expected, "lessThan": value < expected,
                "atLeast": value >= expected, "atMost": value <= expected}[op]
    if kind == "boolean":
        return value is expected
    if value is None:
        return False
    day = _as_day(value)
    today = utcnow().astimezone(ZURICH).date()
    monday = today - dt.timedelta(days=today.weekday())
    checks = {
        "today": lambda: day == today,
        "thisWeek": lambda: monday <= day < monday + dt.timedelta(days=7),
        "thisMonth": lambda: (day.year, day.month) == (today.year, today.month),
        "thisYear": lambda: day.year == today.year,
        "inTheLastDays": lambda: today - dt.timedelta(days=expected) <= day <= today,
        "moreThanDaysAgo": lambda: day < today - dt.timedelta(days=expected),
        "on": lambda: day == expected,
        "before": lambda: day < expected,
        "after": lambda: day > expected,
    }
    return checks[op]()


@pytest.mark.parametrize("field", list(SPECS))
def test_every_condition_of_every_field_filters_to_the_right_rows(client, stock, field):
    tenant_id, items = stock
    spec = SPECS[field]
    headers = _headers(tenant_id, AccessRole.INVENTORY)
    for op, wire, expected in _cases(spec, items):
        predicate = f"{field}:{op}" + (f":{wire}" if wire is not None else "")
        response = client.get("/v1/inventory/stock-items", params={"filter": predicate, "limit": 50}, headers=headers)
        assert response.status_code == 200, (predicate, response.text)
        got = {item["id"] for item in response.json()["items"]}
        want = {str(item_id) for item_id, item in items.items() if _matches(spec, op, expected, item)}
        assert got == want, predicate
        assert response.json()["total"] == len(want), predicate


def test_predicates_are_anded_and_a_legacy_parameter_is_one_of_them(client, stock):
    tenant_id, items = stock
    headers = _headers(tenant_id, AccessRole.INVENTORY)
    response = client.get(
        "/v1/inventory/stock-items",
        params=[("filter", "listPrice:atLeast:1000"), ("filter", "odometerKm:lessThan:50000"),
                ("lifecycle_status", "in_stock")],
        headers=headers,
    )
    want = {
        str(i) for i, item in items.items()
        if item.list_price is not None and item.list_price >= 1000 and item.odometer_km is not None
        and item.odometer_km < 50000 and item.lifecycle_status is LifecycleStatus.IN_STOCK
    }
    assert {item["id"] for item in response.json()["items"]} == want


def test_a_text_filter_takes_wildcards_and_colons_literally(client, stock):
    tenant_id, _items = stock
    headers = _headers(tenant_id, AccessRole.INVENTORY)
    for value in ("%", "_", "a:b"):
        response = client.get("/v1/inventory/stock-items", params={"filter": f"supplierName:contains:{value}"}, headers=headers)
        assert response.status_code == 200 and response.json()["items"] == [], value


# --- 422 outside the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"filter": "colour:is:red"}, "unknown_field"),
        ({"filter": "listPrice:contains:5"}, "operator_not_allowed"),
        ({"filter": "listPrice"}, "filter_malformed"),
        ({"filter": "listPrice:atLeast:12'000"}, "invalid_filter_value"),
        ({"filter": "listPrice:between:9,1"}, "invalid_filter_value"),
        ({"filter": "condition:is:occasion"}, "invalid_filter_value"),
        ({"filter": "updatedAt:on:15.11.2026"}, "invalid_filter_value"),
        # Review of b7ec3c4: the day after 9999-12-31 and year 1 in UTC overflowed into a 500.
        ({"filter": "purchaseDate:after:9999-12-31"}, "invalid_filter_value"),
        ({"filter": "updatedAt:before:0001-01-01"}, "invalid_filter_value"),
        ({"filter": "updatedAt:inTheLastDays:36501"}, "invalid_filter_value"),
        ({"filter": "updatedAt:today:1"}, "invalid_filter_value"),
        ({"filter": "isInvoiceable:is:ja"}, "invalid_filter_value"),
        ({"sort": "colour:asc"}, "unknown_field"),
        ({"sort": "listPrice:up"}, "sort_malformed"),
    ],
)
def test_anything_outside_the_registry_is_422_with_its_reason(client, params, reason):
    response = client.get("/v1/inventory/stock-items", params=params, headers=_headers(uuid.uuid4(), AccessRole.INVENTORY))

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason


def test_a_cursor_from_another_sort_is_refused_not_a_500(client, stock):
    """KAN-182, kept inside the layer — a money cursor replayed on a date sort."""

    tenant_id, _items = stock
    headers = _headers(tenant_id, AccessRole.INVENTORY)
    first = client.get("/v1/inventory/stock-items", params={"sort": "listPrice:asc", "limit": 2}, headers=headers)
    cursor = first.json()["nextCursor"]
    assert cursor is not None

    replay = client.get(
        "/v1/inventory/stock-items", params={"sort": "purchaseDate:asc", "limit": 2, "cursor": cursor}, headers=headers
    )
    assert replay.status_code == 400, replay.text
    assert replay.json()["error"]["details"]["reason"] == "cursor_invalid"

"""KAN-161: server-side sort (U-02) and a footer count (U-07) on the
vehicle list's one search box, `GET /v1/vehicle-mdm/search`. Every grid
column is sortable, each backed by an index (U-03). The heaviest coverage
is the cursor walk under a non-default sort — that is where a keyset
predicate bug would show (rows duplicated or skipped across pages).
"""

import uuid

import pytest

from app.core.auth import create_access_token
from app.vehicle.models.vehicle_mdm import CatalogueMatchStatus, VehicleMdm, VehicleStatus

SORTABLE = ["vehicleNumber", "vin", "stammnummer", "catalogueMatchStatus", "vehicleStatus"]


def _bearer() -> dict[str, str]:
    tid = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tid, group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset(), is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _seed(db_session, rows: list[dict]) -> None:
    for row in rows:
        db_session.add(VehicleMdm(**row))
    db_session.commit()


# Deliberately not in vehicle-number order on any other column, with
# repeated statuses and some null stammnummern, so every sort has ties
# and a null tail to page across.
FLEET = [
    {"vehicle_number": "F-000001", "vin": "VINCCCCCCCCCCCC03", "stammnummer": "300000000",
     "vehicle_status": VehicleStatus.STOLEN, "catalogue_match_status": CatalogueMatchStatus.UNVERIFIED},
    {"vehicle_number": "F-000002", "vin": "VINAAAAAAAAAAAA01", "stammnummer": None,
     "vehicle_status": VehicleStatus.ACTIVE, "catalogue_match_status": CatalogueMatchStatus.MATCHED},
    {"vehicle_number": "F-000003", "vin": "VINEEEEEEEEEEEE05", "stammnummer": "100000000",
     "vehicle_status": VehicleStatus.SCRAPPED, "catalogue_match_status": CatalogueMatchStatus.UNVERIFIED},
    {"vehicle_number": "F-000004", "vin": "VINBBBBBBBBBBBB02", "stammnummer": None,
     "vehicle_status": VehicleStatus.ACTIVE, "catalogue_match_status": CatalogueMatchStatus.UNVERIFIED},
    {"vehicle_number": "F-000005", "vin": "VINDDDDDDDDDDDD04", "stammnummer": "200000000",
     "vehicle_status": VehicleStatus.EXPORTED, "catalogue_match_status": CatalogueMatchStatus.MATCHED},
]


def _search(client, **query) -> dict:
    response = client.get("/v1/vehicle-mdm/search", params=query, headers=_bearer())
    assert response.status_code == 200, response.text
    return response.json()


def _walk(client, *, sort: str | None, limit: int, q: str = "") -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(50):  # a broken cursor must not loop forever
        query: dict = {"q": q, "limit": limit}
        if sort:
            query["sort"] = sort
        if cursor:
            query["cursor"] = cursor
        page = _search(client, **query)["filtered"]
        items.extend(page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            return items
    pytest.fail("Pagination did not terminate within 50 pages.")


def _expected(field: str, direction: str) -> list[str]:
    """Vehicle numbers in the order the API must return them: the field
    (nulls last in both directions, FR-UI-01), then id — and ids are
    UUIDv7, so in insertion order, which is FLEET's order here."""

    key = {
        "vehicleNumber": "vehicle_number", "vin": "vin", "stammnummer": "stammnummer",
        "catalogueMatchStatus": "catalogue_match_status", "vehicleStatus": "vehicle_status",
    }[field]

    def value(row: dict):
        v = row[key]
        return v.value if hasattr(v, "value") else v

    present = [r for r in FLEET if value(r) is not None]
    nulls = [r for r in FLEET if value(r) is None]
    present = sorted(present, key=value, reverse=direction == "desc")
    if direction == "desc":
        # reverse=True also reversed equal keys; restore insertion (id) order among ties.
        groups: dict = {}
        for r in present:
            groups.setdefault(value(r), []).append(r)
        present = [r for v in groups for r in sorted(groups[v], key=FLEET.index)]
    return [r["vehicle_number"] for r in present + nulls]


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("field", SORTABLE)
def test_every_column_sorts_both_ways_across_cursor_pages(client, db_session, field, direction):
    _seed(db_session, FLEET)

    items = _walk(client, sort=f"{field}:{direction}", limit=2)

    assert [i["vehicleNumber"] for i in items] == _expected(field, direction)


def test_default_sort_is_vehicle_number_ascending(client, db_session):
    _seed(db_session, list(reversed(FLEET)))

    items = _walk(client, sort=None, limit=2)

    assert [i["vehicleNumber"] for i in items] == [f"F-00000{n}" for n in range(1, 6)]


def test_cursor_walk_returns_every_row_exactly_once(client, db_session):
    _seed(db_session, FLEET)

    items = _walk(client, sort="vehicleStatus:asc,vin:desc", limit=1)

    ids = [i["id"] for i in items]
    assert len(ids) == len(FLEET) == len(set(ids))


def test_count_matches_the_filter(client, db_session):
    _seed(db_session, FLEET)

    everything = _search(client, limit=2)["filtered"]
    assert everything["total"] == 5
    assert everything["totalIsEstimate"] is False
    assert len(everything["items"]) == 2

    narrowed = _search(client, q="VINA", limit=2)["filtered"]
    assert narrowed["total"] == 1
    assert [i["vin"] for i in narrowed["items"]] == ["VINAAAAAAAAAAAA01"]


def test_merged_away_vehicles_are_neither_listed_nor_counted(client, db_session):
    _seed(db_session, FLEET)
    survivor = db_session.query(VehicleMdm).filter_by(vehicle_number="F-000001").one()
    retired = db_session.query(VehicleMdm).filter_by(vehicle_number="F-000002").one()
    retired.merged_into_vehicle_id = survivor.id
    db_session.commit()

    page = _search(client, limit=50)["filtered"]

    assert page["total"] == 4
    assert "F-000002" not in [i["vehicleNumber"] for i in page["items"]]


@pytest.mark.parametrize(
    "sort",
    ["make:asc", "vin:sideways", "vin", "vin:asc,vin:desc", "plate:asc"],
)
def test_unknown_or_malformed_sort_is_422(client, sort):
    response = client.get("/v1/vehicle-mdm/search", params={"sort": sort}, headers=_bearer())

    assert response.status_code == 422, response.text


def test_identifier_resolution_still_resolves_and_the_grid_below_honours_sort(client, db_session):
    """Do-not-touch (FR-V-06/FR-V-16): a VIN-shaped query resolves above
    the grid exactly as before. Since KAN-82 the hit carries no page of
    its own (`filtered` is empty: an unrelated page there misled global
    search); the grid that stays below it on the Vehicles screen is the
    unfiltered list, read with an empty `q` and sorted and counted like
    any other page."""

    vin = "1HGCM82633A004352"
    _seed(db_session, FLEET + [{"vehicle_number": "F-000006", "vin": vin}])

    body = _search(client, q=vin, sort="vin:asc", limit=50)

    assert body["resolved"]["vin"] == vin
    assert body["pickerCandidates"] == []
    assert body["filtered"]["items"] == []
    assert body["filtered"]["total"] == 0

    grid = _search(client, q="", sort="vin:asc", limit=50)
    vins = [i["vin"] for i in grid["filtered"]["items"]]
    assert vins == sorted(vins)
    assert grid["filtered"]["total"] == 6

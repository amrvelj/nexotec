"""KAN-376: every inventory write endpoint depends on the permission-matrix
row that describes it, never on a wider key.

Until KAN-376 one key, `stock_items`, guarded all fifteen inventory writes, so
the matrix's "Stock and pipeline vehicles" write for `sales` (Anto,
2026-10-17) could not be enforced without also letting a sales user book a
purchase, override the notional input tax, post a landed cost, publish at a
marketplace (ADR-062) or reserve a car directly (ADR-047). Each endpoint now
names its own row; `stock_items` covers only the stock item's own record.

Two readings the matrix leaves to the code, stated here so a change to either
is a visible edit: a hand-booked Wagenbuch cost (FR-I-15a) is a landed cost,
so `record_cost` is guarded by `stock_acquisition` ("Stock acquisition,
landed cost, storno") — the `wagenbuch` row is the derived balance sheet,
which nobody writes; and the direct reserve/release endpoints are guarded by
`stock_reservation`, whose matrix cell is "Nobody" (the sales contract
lifecycle reserves in-process), so only platform staff can call them.
"""

import uuid

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.permissions import CAPABILITY_MATRIX
from app.main import app
from tests.route_walk import api_routes

# (method, path) → the matrix row that guards it. A new inventory write
# endpoint fails test_every_inventory_write_endpoint_names_its_matrix_row
# until it is entered here with its row.
EXPECTED_CAPABILITY: dict[tuple[str, str], str] = {
    ("POST", "/v1/inventory/stock-items"): "stock_items",
    ("PATCH", "/v1/inventory/stock-items/{stock_item_id}"): "stock_items",
    ("PUT", "/v1/inventory/stock-items/{stock_item_id}/options"): "stock_items",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/condition"): "stock_condition",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/purchase"): "stock_acquisition",
    ("PATCH", "/v1/inventory/stock-items/{stock_item_id}/purchase/notional-input-tax"): "stock_acquisition",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/ledger-entries"): "stock_acquisition",
    ("PATCH", "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}"): "marketplace_publishing",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/publish"): "marketplace_publishing",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/publishing/{channel}/unpublish"): "marketplace_publishing",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/media"): "marketplace_publishing",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/media/reorder"): "marketplace_publishing",
    ("DELETE", "/v1/inventory/stock-items/{stock_item_id}/media/{media_id}"): "marketplace_publishing",
    ("POST", "/v1/inventory/stock-items/{stock_item_id}/reservations"): "stock_reservation",
    ("POST", "/v1/inventory/reservations/{reservation_id}/release"): "stock_reservation",
}


def _write_capabilities(dependant) -> list[str]:
    """The capability names of every require_write() gate in a route's
    dependency tree, read from the gate's closure (require_write keeps the
    name it was built with)."""

    found: list[str] = []
    for sub in dependant.dependencies:
        gate = sub.call
        if getattr(gate, "__qualname__", "").startswith("require_write."):
            cells = dict(zip(gate.__code__.co_freevars, (c.cell_contents for c in gate.__closure__ or ())))
            found.append(cells["capability"])
        found.extend(_write_capabilities(sub))
    return found


def test_every_inventory_write_endpoint_names_its_matrix_row():
    actual: dict[tuple[str, str], list[str]] = {}
    for path, route in api_routes(app.routes):
        if not path.startswith("/v1/inventory/"):
            continue
        for method in route.methods - {"GET", "HEAD"}:
            actual[(method, path)] = _write_capabilities(route.dependant)

    assert set(actual) == set(EXPECTED_CAPABILITY), "an inventory write endpoint was added or removed"
    wrong = {key: caps for key, caps in actual.items() if caps != [EXPECTED_CAPABILITY[key]]}
    assert wrong == {}, f"guarded by the wrong (or no / more than one) matrix row: {wrong}"
    assert all(cap in CAPABILITY_MATRIX for cap in EXPECTED_CAPABILITY.values())


def _token(tenant_id: uuid.UUID, *roles: AccessRole, is_dealer_manager: bool = False) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset(roles),
        is_dealer_manager=is_dealer_manager,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stock_item(client) -> tuple[uuid.UUID, dict]:
    tenant_id = uuid.uuid4()
    response = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia", "condition": "used"},
        headers=_token(tenant_id, AccessRole.INVENTORY),
    )
    assert response.status_code == 201, response.text
    return tenant_id, response.json()


# The sales-only refusals the ticket names: purchase booking, the notional-
# input-tax override, a Wagenbuch (landed) cost, every publishing write, and
# reserve/release. The permission check runs before the body is validated
# and before the row is looked up, so a placeholder body is enough: anything
# but 403 means the gate let a sales user through.
_SALES_REFUSED = [
    ("POST", "/purchase"),
    ("PATCH", "/purchase/notional-input-tax"),
    ("POST", "/ledger-entries"),
    ("PATCH", "/publishing/autoscout24"),
    ("POST", "/publishing/autoscout24/publish"),
    ("POST", "/publishing/autoscout24/unpublish"),
    ("POST", "/media"),
    ("POST", "/media/reorder"),
    ("DELETE", f"/media/{uuid.uuid4()}"),
    ("POST", "/reservations"),
]


@pytest.mark.parametrize(("method", "suffix"), _SALES_REFUSED, ids=[f"{m} {s}" for m, s in _SALES_REFUSED])
def test_a_sales_user_is_refused_the_writes_their_own_rows_reserve(client, stock_item, method, suffix):
    tenant_id, item = stock_item
    response = client.request(
        method,
        f"/v1/inventory/stock-items/{item['id']}{suffix}",
        json={} if method != "DELETE" else None,
        headers={**_token(tenant_id, AccessRole.SALES), "If-Match": str(item["version"])},
    )
    assert response.status_code == 403, response.text


def test_a_sales_user_is_refused_a_direct_release(client, stock_item):
    tenant_id, _ = stock_item
    response = client.post(
        f"/v1/inventory/reservations/{uuid.uuid4()}/release", headers=_token(tenant_id, AccessRole.SALES)
    )
    assert response.status_code == 403, response.text


def test_a_sales_user_may_create_and_edit_a_stock_item(client):
    """Anto's ruling (2026-10-17): the matrix's "Stock and pipeline vehicles"
    row is inventory AND sales — create, patch, options and condition."""

    tenant_id = uuid.uuid4()
    sales = _token(tenant_id, AccessRole.SALES)

    created = client.post(
        "/v1/inventory/stock-items", json={"vehicleLabel": "Škoda Octavia", "condition": "new"}, headers=sales
    )
    assert created.status_code == 201, created.text
    item = created.json()

    patched = client.patch(
        f"/v1/inventory/stock-items/{item['id']}",
        json={"vehicleLabel": "Škoda Octavia Combi"},
        headers={**sales, "If-Match": str(item["version"])},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["vehicleLabel"] == "Škoda Octavia Combi"

    options = client.put(
        f"/v1/inventory/stock-items/{item['id']}/options",
        json={"basePrice": "30000.00", "options": [{"code": "PANO", "label": "Panoramadach", "price": "1200.00"}]},
        headers={**sales, "If-Match": str(patched.json()["version"])},
    )
    assert options.status_code == 200, options.text

    condition = client.post(
        f"/v1/inventory/stock-items/{item['id']}/condition",
        json={"condition": "demo"},
        headers={**sales, "If-Match": str(options.json()["version"])},
    )
    assert condition.status_code == 200, condition.text
    assert condition.json()["condition"] == "demo"


def test_an_inventory_user_still_books_a_landed_cost(client, stock_item):
    """The hand-booked Wagenbuch cost (FR-I-15a, the stock detail page's cost
    dialog) stays open to inventory: it is a landed cost, not a write to the
    derived Wagenbuch, which nobody writes."""

    tenant_id, item = stock_item
    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/ledger-entries",
        json={
            "category": "aufbereitung",
            "amount": "250.00",
            "occurredAt": "2026-10-20T10:00:00Z",
            "sourceRef": f"manual:{uuid.uuid4()}",
        },
        headers=_token(tenant_id, AccessRole.INVENTORY),
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("roles", "is_dealer_manager"),
    [((AccessRole.INVENTORY,), False), ((AccessRole.SALES,), False), ((), True)],
    ids=["inventory", "sales", "dealer-manager"],
)
def test_nobody_but_platform_staff_reserves_directly(client, stock_item, roles, is_dealer_manager):
    """The matrix's stock-reservation write is "Nobody": the sales contract
    lifecycle reserves in-process (ADR-047), and the manager flag does not
    open the row (manager_can_write=False)."""

    tenant_id, item = stock_item
    refused = client.post(
        f"/v1/inventory/stock-items/{item['id']}/reservations",
        json={"contractId": str(uuid.uuid4())},
        headers=_token(tenant_id, *roles, is_dealer_manager=is_dealer_manager),
    )
    assert refused.status_code == 403, refused.text

    allowed = client.post(
        f"/v1/inventory/stock-items/{item['id']}/reservations",
        json={"contractId": str(uuid.uuid4())},
        headers=_token(tenant_id, AccessRole.PLATFORM_ADMIN),
    )
    assert allowed.status_code == 201, allowed.text

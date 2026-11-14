"""WP-7 PR-7: group listing API — /groups/mine resolves group_id from the
JWT, never a client-supplied one."""

import uuid

from app.core.auth import AccessRole, create_access_token
from app.platform.organisation.models.dealership import DealerGroup


def _token(group_id: uuid.UUID, role: AccessRole | None = None) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), group_id=group_id,
        roles=frozenset({role}) if role else frozenset(),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_groups_mine_404s_when_group_read_not_enabled(client, db_session):
    group = DealerGroup(name="Read-disabled", group_read_enabled=False)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get("/v1/inventory/groups/mine/stock-items", headers=_bearer(token))
    assert response.status_code == 404, response.text


def test_groups_mine_200s_when_enabled_even_with_no_stock(client, db_session):
    group = DealerGroup(name="Read-enabled", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get("/v1/inventory/groups/mine/stock-items", headers=_bearer(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "nextCursor": None, "total": 0, "totalIsEstimate": False}


def test_groups_mine_accepts_sort_search_and_cursor_parameters(client, db_session):
    """KAN-152 item 2 — the group endpoint takes the same sort/limit/cursor
    contract as the tenant stock grid, plus q."""

    group = DealerGroup(name="Paged", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get(
        "/v1/inventory/groups/mine/stock-items",
        params={"sort": "stockNumber:asc", "limit": 10, "q": "Golf"},
        headers=_bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["nextCursor"] is None


def test_groups_mine_rejects_an_unsortable_field_with_422(client, db_session):
    group = DealerGroup(name="Paged", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()

    token = _token(group.id, AccessRole.INVENTORY)
    response = client.get(
        "/v1/inventory/groups/mine/stock-items", params={"sort": "listPrice:asc"}, headers=_bearer(token)
    )
    assert response.status_code == 422, response.text


def test_groups_mine_sorts_on_exactly_the_tenant_grids_fields(client, db_session):
    """The group grid's columns (stockGroupColumns.test.ts) offer exactly
    these sorts; a field dropped here would 422 a header click there."""

    from app.inventory.api.stock_items import STOCK_ITEM_SORT_FIELDS

    assert set(STOCK_ITEM_SORT_FIELDS) == {"stockNumber", "vin", "updatedAt", "createdAt"}

    group = DealerGroup(name="Paged", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()
    token = _token(group.id, AccessRole.INVENTORY)
    for field in STOCK_ITEM_SORT_FIELDS:
        for direction in ("asc", "desc"):
            response = client.get(
                "/v1/inventory/groups/mine/stock-items",
                params={"sort": f"{field}:{direction}"},
                headers=_bearer(token),
            )
            assert response.status_code == 200, (field, direction, response.text)


# --- KAN-565: the Betrieb filter (ADR-055 «the dealership as a filter») ---------


def _group_of_two(db_session):
    """A group with two Betriebe holding one car each, and a third Betrieb in
    another group (its read on too) holding one."""

    from app.inventory.models.stock_item import StockItemCondition
    from app.inventory.schemas.stock_item import StockItemCreate
    from app.inventory.services.stock_item import create_stock_item
    from tests.test_inventory_group_listing import _make_dealership

    group = DealerGroup(name="Two Betriebe", group_read_enabled=True)
    elsewhere = DealerGroup(name="Another group", group_read_enabled=True)
    db_session.add_all([group, elsewhere])
    db_session.commit()
    zurich = _make_dealership(db_session, group, license_number="ZH-9")
    bern = _make_dealership(db_session, group, license_number="BE-1")
    stranger = _make_dealership(db_session, elsewhere, license_number="GE-1")
    for dealer, label in ((zurich, "Zurich car"), (bern, "Bern car"), (stranger, "Geneva car")):
        create_stock_item(
            db_session, tenant_id=dealer.id,
            data=StockItemCreate(vehicle_label=label, condition=StockItemCondition.USED),
            actor_id=uuid.uuid4(),
        )
    return group, zurich, bern, stranger


def _labels(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {item["vehicleLabel"] for item in response.json()["items"]}


def test_the_betrieb_filter_narrows_the_group_listing(client, db_session):
    group, zurich, bern, _stranger = _group_of_two(db_session)
    headers = _bearer(_token(group.id, AccessRole.INVENTORY))
    url = "/v1/inventory/groups/mine/stock-items"

    assert _labels(client.get(url, headers=headers)) == {"Zurich car", "Bern car"}
    only_zurich = client.get(url, params={"dealershipId": str(zurich.id)}, headers=headers)
    assert _labels(only_zurich) == {"Zurich car"}
    assert only_zurich.json()["total"] == 1
    assert _labels(client.get(url, params={"dealershipIdNot": str(zurich.id)}, headers=headers)) == {"Bern car"}
    # Repeatable: both named, both kept.
    both = client.get(url, params=[("dealershipId", str(zurich.id)), ("dealershipId", str(bern.id))], headers=headers)
    assert _labels(both) == {"Zurich car", "Bern car"}
    # The explicit-group route takes the same filter.
    explicit = client.get(f"/v1/inventory/groups/{group.id}/stock-items", params={"dealershipId": str(bern.id)}, headers=headers)
    assert _labels(explicit) == {"Bern car"}


def test_a_betrieb_outside_the_group_yields_an_empty_page_never_a_refusal(client, db_session):
    group, zurich, _bern, stranger = _group_of_two(db_session)
    headers = _bearer(_token(group.id, AccessRole.INVENTORY))
    url = "/v1/inventory/groups/mine/stock-items"

    for outsider in (stranger.id, uuid.uuid4()):
        response = client.get(url, params={"dealershipId": str(outsider)}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "nextCursor": None, "total": 0, "totalIsEstimate": False}
    # Mixed with one of the group's own, only the own one counts.
    mixed = client.get(url, params=[("dealershipId", str(zurich.id)), ("dealershipId", str(stranger.id))], headers=headers)
    assert _labels(mixed) == {"Zurich car"}
    # An exclusion of an outsider excludes nothing of the group's.
    assert _labels(client.get(url, params={"dealershipIdNot": str(stranger.id)}, headers=headers)) == {"Zurich car", "Bern car"}


def test_the_betrieb_options_are_the_groups_roster_by_label(client, db_session):
    group, zurich, bern, _stranger = _group_of_two(db_session)

    response = client.get("/v1/inventory/groups/mine/dealerships", headers=_bearer(_token(group.id, AccessRole.INVENTORY)))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {"id": str(bern.id), "label": bern.legal_name},
            {"id": str(zurich.id), "label": zurich.legal_name},
        ]
    }


def test_the_betrieb_options_refuse_whom_the_listing_refuses_with_404(client, db_session):
    group, *_ = _group_of_two(db_session)
    url = "/v1/inventory/groups/mine/dealerships"

    # A role the group read is not granted to (no inventory role, no manager flag).
    assert client.get(url, headers=_bearer(_token(group.id, AccessRole.SALES))).status_code == 404
    # The group's read switched off.
    group.group_read_enabled = False
    db_session.commit()
    assert client.get(url, headers=_bearer(_token(group.id, AccessRole.INVENTORY))).status_code == 404

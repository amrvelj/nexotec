"""KAN-429: every inventory and integration route that checks If-Match loads
its row `for_update` (as Sales does since KAN-311). Unlocked, two saves sent
with the same version both passed the check before either committed, and
both landed — the lost update If-Match exists to stop.

Per route, deterministically: a session holds the row locked and saves it
(as a concurrent request with the same If-Match would); the real route, sent
from a thread with that same version, is still blocked after 0.8 s; once the
holder commits, the route reads the saved row and is refused as stale (409),
and the holder's save stands. One 200 (the holder's), one 409."""

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, create_access_token
from app.integration.models.connection import ConnectionEnvironment, ConnectionScope, IntegrationConnection
from app.integration.services import connections as connection_service
from app.integration.services import providers as provider_service
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.models.stock_item_publishing import MarketplaceChannel
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import publishing as publishing_service
from app.inventory.services import stock_item as stock_item_service
from tests.test_integration_gateway import _make_provider
from tests.test_sales_contract_edit import _postgres_only_row_locks


def _headers(tenant_id: uuid.UUID, *roles: AccessRole, manager: bool = False) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset(roles), is_dealer_manager=manager,
    )
    return {"Authorization": f"Bearer {token}"}


@dataclass
class _Case:
    """A route under test: its request, and how a concurrent save of the
    same row looks from a session of its own (lock, change, version += 1)."""

    method: str
    path: str
    body: dict | None
    headers: dict[str, str]
    version: int
    hold: Callable[[Session], None]


def _stock_item_case(db_session, method: str, suffix: str, body: dict | None) -> _Case:
    tenant_id = uuid.uuid4()
    item = stock_item_service.create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED), actor_id=uuid.uuid4(),
    )

    def hold(holding: Session) -> None:
        held = stock_item_service.get_stock_item_for_update_or_404(holding, tenant_id, item.id)
        held.vehicle_label = "Škoda Octavia RS"
        held.version += 1

    return _Case(
        method, f"/v1/inventory/stock-items/{item.id}{suffix}", body,
        _headers(tenant_id, AccessRole.INVENTORY), item.version, hold,
    )


def _listing_text_case(db_session) -> _Case:
    tenant_id = uuid.uuid4()
    item = stock_item_service.create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED), actor_id=uuid.uuid4(),
    )
    row = publishing_service.get_or_create_publishing(db_session, item, MarketplaceChannel.AUTOSCOUT24)
    db_session.commit()

    def hold(holding: Session) -> None:
        held_item = stock_item_service.get_stock_item_or_404(holding, tenant_id, item.id)
        held = publishing_service.get_or_create_publishing_for_update(holding, held_item, MarketplaceChannel.AUTOSCOUT24)
        held.zusatztitel = "Anna's title"
        held.version += 1

    return _Case(
        "patch", f"/v1/inventory/stock-items/{item.id}/publishing/autoscout24", {"zusatztitel": "Beat's title"},
        _headers(tenant_id, AccessRole.INVENTORY), row.version, hold,
    )


def _connection_case(db_session, method: str, suffix: str, body: dict | None) -> _Case:
    tenant_id = uuid.uuid4()
    provider = _make_provider(db_session)
    connection = IntegrationConnection(
        tenant_id=tenant_id, provider_id=provider.id, scope=ConnectionScope.TENANT, display_name="auto-i-dat",
        environment=ConnectionEnvironment.SANDBOX,
    )
    db_session.add(connection)
    db_session.commit()

    def hold(holding: Session) -> None:
        held = connection_service.get_connection_for_update_or_404(
            holding, tenant_id=tenant_id, connection_id=connection.id
        )
        held.display_name = "auto-i-dat (Anna)"
        held.version += 1

    return _Case(
        method, f"/v1/integrations/connections/{connection.id}{suffix}", body,
        _headers(tenant_id, manager=True), connection.version, hold,
    )


def _provider_case(db_session) -> _Case:
    provider = _make_provider(db_session)

    def hold(holding: Session) -> None:
        held = provider_service.get_provider_for_update_or_404(holding, provider.id)
        held.display_name = "auto-i-dat (Anna)"
        held.version += 1

    return _Case(
        "patch", f"/v1/integrations/providers/{provider.id}", {"displayName": "auto-i-dat (Beat)"},
        _headers(uuid.uuid4(), AccessRole.PLATFORM_ADMIN), provider.version, hold,
    )


_PURCHASE = {
    "supplierName": "Garage Muster AG", "supplierIsVatRegistered": True,
    "purchasePrice": "18000.00", "purchaseDate": "2026-10-01",
}

_ROUTES: dict[str, Callable] = {
    "stock_item_patch": lambda db: _stock_item_case(db, "patch", "", {"odometerKm": 12000}),
    "stock_item_condition": lambda db: _stock_item_case(db, "post", "/condition", {"condition": "demo"}),
    "stock_item_purchase": lambda db: _stock_item_case(db, "post", "/purchase", _PURCHASE),
    "stock_item_notional_input_tax": lambda db: _stock_item_case(
        db, "patch", "/purchase/notional-input-tax", {"applicable": False, "reason": "Händler ist Margenbesteuerer"}
    ),
    "stock_item_options": lambda db: _stock_item_case(
        db, "put", "/options", {"basePrice": "30000.00", "options": [{"code": "PA1", "label": "PA1", "price": "1.00"}]}
    ),
    "listing_text": _listing_text_case,
    "connection_patch": lambda db: _connection_case(db, "patch", "", {"displayName": "auto-i-dat (Beat)"}),
    "connection_disable": lambda db: _connection_case(db, "post", "/disable", None),
    "connection_enable": lambda db: _connection_case(db, "post", "/enable", None),
    "connection_delete": lambda db: _connection_case(db, "delete", "", {"confirm": True}),
    "provider_patch": _provider_case,
}


@_postgres_only_row_locks
@pytest.mark.parametrize("route", list(_ROUTES))
def test_a_save_with_the_same_if_match_waits_for_the_one_in_progress_and_is_refused(client, db_session, engine, route):
    case = _ROUTES[route](db_session)
    result = {}

    def send() -> None:
        result["response"] = client.request(
            case.method, case.path, json=case.body, headers={**case.headers, "If-Match": str(case.version)}
        )

    holding = Session(bind=engine)
    try:
        case.hold(holding)
        holding.flush()
        sender = threading.Thread(target=send)
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        holding.commit()
        sender.join(10)
    finally:
        holding.close()

    assert blocked_while_held, "the route read the row without waiting for the save in progress"
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"currentVersion": case.version + 1, "ifMatchVersion": case.version}

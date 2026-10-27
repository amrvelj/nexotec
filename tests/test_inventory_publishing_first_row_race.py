"""KAN-457: a channel's publishing row is created on first use. Two first
users of a channel that has no row yet both missed it and both inserted it;
the second insert waited for the first and then hit
`uq_stock_item_publishing_stock_item_id_channel` — an IntegrityError, a 500.

In the shape of `tests/test_if_match_row_locks.py`: a session inserts the row
and saves it (as the first of two concurrent requests would), the real route
sent from a thread is still blocked after 0.8 s, and once the holder commits
the route takes the holder's row — a stale If-Match is refused (409), a GET
returns that row. Never a 500, and one row."""

import threading
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import AccessRole, create_access_token
from app.inventory.models.stock_item import StockItemCondition
from app.inventory.models.stock_item_publishing import MarketplaceChannel, StockItemPublishing
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import publishing as publishing_service
from app.inventory.services import stock_item as stock_item_service
from tests.test_sales_contract_edit import _postgres_only_row_locks


def _headers(tenant_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(),
        roles=frozenset({AccessRole.INVENTORY}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}"}


def _item_without_publishing_row(db_session):
    tenant_id = uuid.uuid4()
    item = stock_item_service.create_stock_item(
        db_session, tenant_id=tenant_id,
        data=StockItemCreate(vehicle_label="Škoda Octavia", condition=StockItemCondition.USED), actor_id=uuid.uuid4(),
    )
    db_session.commit()
    assert db_session.scalar(select(StockItemPublishing).where(StockItemPublishing.stock_item_id == item.id)) is None
    return tenant_id, item


def _race(engine, tenant_id, item, send) -> tuple[bool, dict, uuid.UUID, int]:
    """The holder inserts the row and saves it; `send` runs the route meanwhile."""

    result: dict = {}
    holding = Session(bind=engine)
    try:
        held_item = stock_item_service.get_stock_item_or_404(holding, tenant_id, item.id)
        held = publishing_service.get_or_create_publishing_for_update(holding, held_item, MarketplaceChannel.AUTOSCOUT24)
        first_version = held.version
        held.zusatztitel = "Anna's title"
        held.version += 1
        holding.flush()
        held_id = held.id
        sender = threading.Thread(target=lambda: result.update(response=send()))
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        holding.commit()
        sender.join(10)
    finally:
        holding.close()
    return blocked_while_held, result, held_id, first_version


def _rows(db_session, item) -> int:
    db_session.expire_all()
    return db_session.scalar(
        select(func.count()).select_from(StockItemPublishing).where(StockItemPublishing.stock_item_id == item.id)
    )


@_postgres_only_row_locks
def test_two_first_listing_text_patches_are_one_save_and_one_409(client, db_session, engine):
    tenant_id, item = _item_without_publishing_row(db_session)
    path = f"/v1/inventory/stock-items/{item.id}/publishing/autoscout24"
    # The If-Match a client sends for a row it has not read: the new row's own version.
    version = StockItemPublishing.__table__.c.version.default.arg

    blocked, result, _, first_version = _race(
        engine, tenant_id, item,
        lambda: client.patch(path, json={"zusatztitel": "Beat's title"}, headers={**_headers(tenant_id), "If-Match": str(version)}),
    )

    assert first_version == version
    assert blocked, "the route inserted the row without waiting for the first insert in progress"
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == {"currentVersion": version + 1, "ifMatchVersion": version}
    assert _rows(db_session, item) == 1
    saved = db_session.scalar(select(StockItemPublishing).where(StockItemPublishing.stock_item_id == item.id))
    assert saved.zusatztitel == "Anna's title"


@_postgres_only_row_locks
def test_a_first_get_beside_a_first_insert_returns_that_row(client, db_session, engine):
    tenant_id, item = _item_without_publishing_row(db_session)
    path = f"/v1/inventory/stock-items/{item.id}/publishing/autoscout24"

    blocked, result, held_id, _ = _race(engine, tenant_id, item, lambda: client.get(path, headers=_headers(tenant_id)))

    assert blocked
    response = result["response"]
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(held_id)
    assert response.json()["zusatztitel"] == "Anna's title"
    assert _rows(db_session, item) == 1

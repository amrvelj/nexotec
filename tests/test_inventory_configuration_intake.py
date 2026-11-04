"""KAN-463 — every stock vehicle enters through the configurator (Anto,
2026-10-26; option A 2026-10-28; decisions 1a and 2a 2026-10-30).

`POST /v1/inventory/stock-items` refuses a car without the configuration the
configurator produced — the retired hand-typed intake created a label with no
vehicle and no technical data. An item that intake already created is repaired
with «Konfiguration zuordnen»: `POST …/stock-items/{id}/configuration`,
If-Match, audited, refused when one is already linked. The server-side paths
(FR-I-02/02b consumers, the ADR-091 legacy import) build their own
`StockItemCreate` and are not this door."""

import threading
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token, decode_token_claims
from app.core.outbox_model import OutboxMessage
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services import stock_item as stock_item_service
from app.inventory.services.stock_item import create_stock_item
from app.vehicle.models.configuration import ConfigurationMode
from app.vehicle.services import configuration as configuration_service
from tests.configuration_test_support import stock_configuration_id
from tests.test_sales_contract_edit import _postgres_only_row_locks
from tests.token_support import dealer_group_of

VIN = "WVWZZZ1KZAW000001"


def _headers(tenant_id: uuid.UUID, role: AccessRole = AccessRole.INVENTORY, **extra: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({role}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _hand_typed_item(db_session, tenant_id: uuid.UUID, *, vin: str | None = None) -> StockItem:
    """What the retired «Fahrzeug aufnehmen» form left behind: a label, a
    condition, perhaps a VIN — no configuration."""

    return create_stock_item(
        db_session, tenant_id=tenant_id, actor_id=None,
        data=StockItemCreate(vehicle_label="VW Golf 8 GTI", condition=StockItemCondition.USED, vin=vin),
    )


def _attach(client, item: StockItem, configuration_id: str, headers: dict[str, str], *, version: int | None = None):
    return client.post(
        f"/v1/inventory/stock-items/{item.id}/configuration",
        json={"configurationId": configuration_id},
        headers={**headers, "If-Match": str(item.version if version is None else version)},
    )


def _attach_audit_rows(db_session, item_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == item_id, AuditEvent.action == "configuration_attached")
        )
    )


def _configuration_with_vin(db_session, headers: dict[str, str], vin: str) -> str:
    configuration_id = uuid.UUID(stock_configuration_id(db_session, headers))
    configuration = configuration_service.get_configuration_or_404(
        db_session, tenant_id=_tenant_of(headers), configuration_id=configuration_id
    )
    configuration.vin = vin
    db_session.commit()
    return str(configuration_id)


def _tenant_of(headers: dict[str, str]) -> uuid.UUID:
    return uuid.UUID(decode_token_claims(headers["Authorization"].removeprefix("Bearer "))["tenant_id"])


# --- the intake ----------------------------------------------------------------


def test_a_create_without_a_configuration_is_refused_and_writes_nothing(client, db_session):
    headers = _headers(uuid.uuid4())

    for body in ({"vehicleLabel": "VW Golf", "condition": "used"}, {"vehicleLabel": "VW Golf", "condition": "used", "configurationId": None}):
        refused = client.post("/v1/inventory/stock-items", json=body, headers=headers)

        assert refused.status_code == 422, refused.text
        errors = refused.json()["error"]["details"]["errors"]
        assert [e["type"] for e in errors] == ["configuration_required"]
    assert db_session.scalar(select(func.count()).select_from(StockItem)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


def test_a_create_with_a_vin_but_no_configuration_is_refused_too(client, db_session):
    """The hand-typed form's «an Lager» path: a car with a known VIN taken
    straight into stock. It needs a configuration like every other car."""

    refused = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "VW Golf", "condition": "used", "vin": VIN},
        headers=_headers(uuid.uuid4()),
    )

    assert refused.status_code == 422, refused.text
    assert db_session.scalar(select(func.count()).select_from(StockItem)) == 0


def test_a_create_with_the_configuration_is_a_pipeline_item_pointing_at_it(client, db_session):
    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers, mode=ConfigurationMode.BUILD)

    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia Combi", "condition": "new", "configurationId": configuration_id},
        headers=headers,
    )

    assert created.status_code == 201, created.text
    assert created.json()["configurationId"] == configuration_id
    assert created.json()["configurationLabel"]
    assert created.json()["lifecycleStatus"] == "pipeline"


def test_the_openapi_says_the_configuration_is_required(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]["StockItemCreateRequest"]

    assert "configurationId" in schema["required"]
    assert "configuration_required" in schema["properties"]["configurationId"]["description"]


def test_the_server_side_create_still_takes_an_item_without_a_configuration(db_session):
    """Decision 1a: enforced at the endpoint only — the FR-I-02/02b consumers
    and the ADR-091 legacy import create their own items."""

    item = _hand_typed_item(db_session, uuid.uuid4())

    assert item.configuration_id is None


# --- «Konfiguration zuordnen» ----------------------------------------------------


def test_attaching_a_configuration_links_it_once_with_an_audit_row(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    item = _hand_typed_item(db_session, tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)

    linked = _attach(client, item, configuration_id, headers)

    assert linked.status_code == 200, linked.text
    body = linked.json()
    assert body["configurationId"] == configuration_id
    assert body["configurationLabel"]
    assert body["version"] == item.version + 1
    [audit] = _attach_audit_rows(db_session, item.id)
    # KAN-556 (ADR-117 (2)): the car's name follows the configuration it took.
    assert audit.before == {"configurationId": None, "vehicleLabel": "VW Golf 8 GTI"}
    assert audit.after == {
        "configurationId": configuration_id,
        "configurationLabel": body["configurationLabel"],
        "vehicleLabel": body["configurationLabel"],
    }
    assert audit.tenant_id == tenant_id


def test_attaching_to_an_item_that_already_has_one_is_refused(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    item = _hand_typed_item(db_session, tenant_id)
    first = stock_configuration_id(db_session, headers)
    assert _attach(client, item, first, headers).status_code == 200

    refused = _attach(client, item, stock_configuration_id(db_session, headers), headers, version=item.version + 1)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == {"reason": "configuration_already_linked", "configurationId": first}
    assert len(_attach_audit_rows(db_session, item.id)) == 1


def test_attaching_needs_if_match_and_refuses_a_stale_version(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    item = _hand_typed_item(db_session, tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)

    without = client.post(
        f"/v1/inventory/stock-items/{item.id}/configuration", json={"configurationId": configuration_id}, headers=headers
    )
    stale = _attach(client, item, configuration_id, headers, version=item.version + 1)

    assert without.status_code == 400, without.text
    assert stale.status_code == 409, stale.text
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).configuration_id is None
    assert _attach_audit_rows(db_session, item.id) == []


def test_attaching_another_betriebs_configuration_or_item_is_404(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    other_headers = _headers(uuid.uuid4())
    item = _hand_typed_item(db_session, tenant_id)

    foreign_configuration = _attach(client, item, stock_configuration_id(db_session, other_headers), headers)
    foreign_item = _attach(client, item, stock_configuration_id(db_session, other_headers), other_headers)

    assert foreign_configuration.status_code == 404, foreign_configuration.text
    assert foreign_item.status_code == 404, foreign_item.text


def test_attaching_a_configuration_of_another_car_is_refused(client, db_session):
    """Both carry a VIN and they differ: the configuration describes another
    car, so linking it would show the wrong technical data."""

    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    item = _hand_typed_item(db_session, tenant_id, vin=VIN)
    other_car = _configuration_with_vin(db_session, headers, "WVWZZZ1KZAW000002")
    same_car = _configuration_with_vin(db_session, headers, VIN)

    refused = _attach(client, item, other_car, headers)
    linked = _attach(client, item, same_car, headers)

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"field": "configurationId", "reason": "configuration_vin_differs"}
    assert linked.status_code == 200, linked.text


def test_a_retried_attach_replays_its_answer_instead_of_a_version_conflict(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())})
    item = _hand_typed_item(db_session, tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)

    first = _attach(client, item, configuration_id, headers)
    retried = _attach(client, item, configuration_id, headers)

    assert first.status_code == 200, first.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == first.json()
    assert len(_attach_audit_rows(db_session, item.id)) == 1


def test_a_sales_user_may_attach_a_configuration(client, db_session):
    """The «Stock and pipeline vehicles» row (KAN-376): the same writers as
    the create."""

    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id, AccessRole.SALES)
    item = _hand_typed_item(db_session, tenant_id)

    linked = _attach(client, item, stock_configuration_id(db_session, headers), headers)

    assert linked.status_code == 200, linked.text



def test_a_create_with_a_configuration_of_another_car_is_refused(client, db_session):
    """The same rule as «Konfiguration zuordnen»: a configuration whose VIN
    is not the body's describes another car, and its technical data would be
    shown on this one (found by KAN-463's review)."""

    headers = _headers(uuid.uuid4())
    other_car = _configuration_with_vin(db_session, headers, "WVWZZZ1KZAW000002")

    refused = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "VW Golf", "condition": "used", "vin": VIN, "configurationId": other_car},
        headers=headers,
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"field": "configurationId", "reason": "configuration_vin_differs"}
    assert db_session.scalar(select(func.count()).select_from(StockItem)) == 0


def test_a_create_with_a_configuration_of_the_same_car_is_taken(client, db_session):
    """KAN-511: the positive control of the create's VIN check — a
    configuration carrying the body's own VIN is the same car."""

    headers = _headers(uuid.uuid4())
    same_car = _configuration_with_vin(db_session, headers, VIN)

    created = client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "VW Golf", "condition": "used", "vin": VIN, "configurationId": same_car},
        headers=headers,
    )

    assert created.status_code == 201, created.text
    assert created.json()["configurationId"] == same_car
    assert created.json()["vin"] == VIN


# --- one configuration, one stock item (KAN-509, FR-C-11) ------------------------


def _held_by(holder: StockItem) -> dict[str, str]:
    return {
        "reason": "configuration_held_by_stock_item",
        "stockItemId": str(holder.id),
        "stockNumber": holder.stock_number,
    }


def _create_with(client, headers: dict[str, str], configuration_id: str):
    return client.post(
        "/v1/inventory/stock-items",
        json={"vehicleLabel": "Škoda Octavia Combi", "condition": "new", "configurationId": configuration_id},
        headers=headers,
    )


def test_a_create_with_a_configuration_another_item_holds_is_refused(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)
    first = _create_with(client, headers, configuration_id)
    assert first.status_code == 201, first.text
    holder = db_session.get(StockItem, uuid.UUID(first.json()["id"]))

    refused = _create_with(client, headers, configuration_id)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == _held_by(holder)
    assert db_session.scalar(select(func.count()).select_from(StockItem)) == 1


def test_attaching_a_configuration_another_item_holds_is_refused(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)
    created = _create_with(client, headers, configuration_id)
    assert created.status_code == 201, created.text
    holder = db_session.get(StockItem, uuid.UUID(created.json()["id"]))
    item = _hand_typed_item(db_session, tenant_id)

    refused = _attach(client, item, configuration_id, headers)

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"] == _held_by(holder)
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).configuration_id is None
    assert _attach_audit_rows(db_session, item.id) == []


def test_another_betriebs_item_holding_the_same_id_does_not_count(client, db_session):
    """The check is the tenant's own stock: nothing about another Betrieb's
    items leaks, and its configurations are 404 here anyway."""

    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)
    other = _hand_typed_item(db_session, uuid.uuid4())
    other.configuration_id = uuid.UUID(configuration_id)
    db_session.commit()

    created = _create_with(client, headers, configuration_id)

    assert created.status_code == 201, created.text


@_postgres_only_row_locks
def test_an_attach_waits_for_a_create_linking_the_same_configuration_and_is_then_refused(client, db_session, engine):
    """Two writers racing: the holder has linked the configuration to its
    item and not committed yet; the attach sent meanwhile waits for it
    (the advisory lock), then sees the holder's item and is refused. Without
    the lock it would not see the uncommitted item and link the
    configuration a second time."""

    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)
    holder = _hand_typed_item(db_session, tenant_id)
    item = _hand_typed_item(db_session, tenant_id)
    result: dict = {}

    holding = Session(bind=engine)
    try:
        held = stock_item_service.get_stock_item_for_update_or_404(holding, tenant_id, holder.id)
        stock_item_service._lock_configuration(holding, tenant_id=tenant_id, configuration_id=uuid.UUID(configuration_id))
        held.configuration_id = uuid.UUID(configuration_id)
        holding.flush()
        sender = threading.Thread(target=lambda: result.update(response=_attach(client, item, configuration_id, headers)))
        sender.start()
        time.sleep(0.8)
        blocked_while_held = sender.is_alive()
        holding.commit()
        sender.join(10)
    finally:
        holding.close()

    assert blocked_while_held, "the attach did not wait for the configuration's lock"
    response = result["response"]
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == _held_by(holder)
    db_session.expire_all()
    assert db_session.get(StockItem, item.id).configuration_id is None

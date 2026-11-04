"""KAN-556 (ADR-117 (2); Anto's ruling on KAN-546, 2026-11-03): a stock car
with a configuration is named by that configuration — `stock_item.vehicle_label`
(the stock list's «Fahrzeug», the detail title) is derived from the
configuration's label at create, at «Konfiguration zuordnen» and by the
nightly `inventory.configuration_labels` refresh, exactly as
`configuration_label` is (KAN-534). The client's text is no longer read for
such a car; an item without a configuration keeps its own. On Postgres."""

import datetime as dt
import uuid

from app.core.audit_model import AuditEvent
from app.inventory.models.stock_item import StockItem
from app.inventory.public import refresh_configuration_labels
from app.vehicle.models.configuration import ConfigurationMode
from app.vehicle.services import configuration as configuration_service
from tests.configuration_test_support import stock_configuration_id
from tests.test_inventory_configuration_intake import _attach, _hand_typed_item, _headers, _tenant_of

# stock_configuration_id's configuration: Škoda / Octavia / Combi.
CONFIGURATION_LABEL = "Škoda Octavia Combi"


def _row(db_session, item_id) -> StockItem:
    db_session.expire_all()
    row = db_session.get(StockItem, uuid.UUID(str(item_id)))
    assert row is not None
    return row


def _create(client, headers, configuration_id: str, **body):
    return client.post(
        "/v1/inventory/stock-items",
        json={"condition": "new", "configurationId": configuration_id, **body},
        headers=headers,
    )


def _rename_configuration(db_session, headers, configuration_id: str) -> None:
    configuration = configuration_service.get_configuration_or_404(
        db_session, tenant_id=_tenant_of(headers), configuration_id=uuid.UUID(configuration_id)
    )
    configuration.model_group_name = "Superb"
    db_session.commit()


def test_a_create_names_the_car_by_its_configuration_and_ignores_the_clients_text(client, db_session):
    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers, mode=ConfigurationMode.BUILD)

    created = _create(client, headers, configuration_id, vehicleLabel="Something the browser typed")

    assert created.status_code == 201, created.text
    assert created.json()["vehicleLabel"] == CONFIGURATION_LABEL
    assert created.json()["configurationLabel"] == CONFIGURATION_LABEL


def test_a_create_needs_no_vehicle_label_at_all(client, db_session):
    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers)

    created = _create(client, headers, configuration_id)

    assert created.status_code == 201, created.text
    assert created.json()["vehicleLabel"] == CONFIGURATION_LABEL


def test_the_openapi_says_the_vehicle_label_is_optional_and_ignored(client):
    schema = client.get("/openapi.json").json()["components"]["schemas"]["StockItemCreateRequest"]

    assert "vehicleLabel" not in schema["required"]
    assert schema["properties"]["vehicleLabel"]["description"].startswith("Ignored")


def test_an_edited_configuration_renames_the_car_with_its_configuration_column(client, db_session):
    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers)
    item = _create(client, headers, configuration_id).json()
    version = _row(db_session, item["id"]).version

    _rename_configuration(db_session, headers, configuration_id)
    assert _row(db_session, item["id"]).vehicle_label == CONFIGURATION_LABEL  # a copy, until the refresh runs

    assert refresh_configuration_labels(db_session) == 1

    row = _row(db_session, item["id"])
    assert (row.vehicle_label, row.configuration_label) == ("Škoda Superb Combi", "Škoda Superb Combi")
    assert row.version == version  # a copy no client writes
    listed = client.get("/v1/inventory/stock-items", headers=headers).json()["items"]
    assert [(i["vehicleLabel"], i["configurationLabel"]) for i in listed] == [("Škoda Superb Combi",) * 2]


def test_a_name_out_of_step_with_a_current_configuration_label_is_brought_into_line(client, db_session):
    """The refresh judges both columns: a row whose name was written before
    KAN-556 (the browser's own label) is renamed even though its
    configuration copy was already current."""

    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers)
    item = _create(client, headers, configuration_id).json()
    row = _row(db_session, item["id"])
    row.vehicle_label = "VW Golf 8 (typed before KAN-556)"
    db_session.commit()

    assert refresh_configuration_labels(db_session) == 1

    assert _row(db_session, item["id"]).vehicle_label == CONFIGURATION_LABEL


def test_an_item_without_a_configuration_keeps_its_own_name(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    legacy = _hand_typed_item(db_session, tenant_id)

    assert refresh_configuration_labels(db_session) == 0
    assert _row(db_session, legacy.id).vehicle_label == "VW Golf 8 GTI"

    # …and it stays the dealer's to edit.
    renamed = client.patch(
        f"/v1/inventory/stock-items/{legacy.id}",
        json={"vehicleLabel": "VW Golf 8 GTI Clubsport"},
        headers={**headers, "If-Match": str(legacy.version)},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["vehicleLabel"] == "VW Golf 8 GTI Clubsport"


def test_a_patch_may_not_rename_a_configured_car(client, db_session):
    headers = _headers(uuid.uuid4())
    configuration_id = stock_configuration_id(db_session, headers)
    item = _create(client, headers, configuration_id).json()

    refused = client.patch(
        f"/v1/inventory/stock-items/{item['id']}",
        json={"vehicleLabel": "Another name"},
        headers={**headers, "If-Match": str(item["version"])},
    )

    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"reason": "vehicle_label_follows_configuration", "field": "vehicleLabel"}
    row = _row(db_session, item["id"])
    assert (row.vehicle_label, row.version) == (CONFIGURATION_LABEL, item["version"])

    # The name resent unchanged beside another field is no rename.
    same = client.patch(
        f"/v1/inventory/stock-items/{item['id']}",
        json={"vehicleLabel": CONFIGURATION_LABEL, "odometerKm": 12},
        headers={**headers, "If-Match": str(item["version"])},
    )
    assert same.status_code == 200, same.text
    assert (same.json()["vehicleLabel"], same.json()["odometerKm"]) == (CONFIGURATION_LABEL, 12)


def test_attaching_a_configuration_renames_the_car_audited(client, db_session):
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id)
    item = _hand_typed_item(db_session, tenant_id)
    configuration_id = stock_configuration_id(db_session, headers)

    linked = _attach(client, item, configuration_id, headers)

    assert linked.status_code == 200, linked.text
    assert (linked.json()["vehicleLabel"], linked.json()["configurationLabel"]) == (CONFIGURATION_LABEL,) * 2
    [audit] = db_session.query(AuditEvent).filter(
        AuditEvent.entity_id == item.id, AuditEvent.action == "configuration_attached"
    ).all()
    assert audit.before == {"configurationId": None, "vehicleLabel": "VW Golf 8 GTI"}
    assert audit.after["vehicleLabel"] == CONFIGURATION_LABEL


def test_the_refresh_leaves_another_betriebs_configuration_alone(client, db_session):
    """KAN-534's rule holds for the name too: a configuration id that names
    another Betrieb's configuration renames nothing."""

    headers = _headers(uuid.uuid4())
    foreign = stock_configuration_id(db_session, _headers(uuid.uuid4()))
    own = stock_configuration_id(db_session, headers)
    item = _create(client, headers, own).json()
    row = _row(db_session, item["id"])
    row.configuration_id = uuid.UUID(foreign)
    row.vehicle_label = "Kept"
    row.configuration_label_refreshed_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    db_session.commit()

    refresh_configuration_labels(db_session)

    assert _row(db_session, item["id"]).vehicle_label == "Kept"

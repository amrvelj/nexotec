"""KAN-557 «VIN korrigieren» (ADR-116 (2)-(3); Anto, 2026-11-03, on KAN-544:
«wir lassen eine Korrektur am Lager zu»). A car in stock whose VIN was wrong
is corrected on the three records that carry it — the stock item, its
vehicle-mdm record and its configuration — each audited with the reason.
Vehicle, which owns the VIN, is asked first on its own transaction (ADR-047);
its refusal leaves the stock item untouched, and a failed stock write puts
Vehicle's records back. Nightly reconciliation reports a car whose three
records disagree (rule 10). On Postgres, over HTTP where the route is under
test.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole, create_access_token
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import ReconciliationAlarm
from app.inventory import reconciliation as inventory_reconciliation
from app.inventory.models.stock_item import StockItem
from app.inventory.services import stock_item as stock_item_service
from app.valuation import reconciliation as valuation_reconciliation
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import correct_vehicle_vin, resolve_vehicle_mdm_by_vin
from tests.test_inventory_purchase_promotes import VIN, _auth, _create, _purchase, _token
from tests.test_valuation_vin_matches_configuration import VIN_DIFFERS, _stored_valuation
from tests.token_support import dealer_group_of

NEW_VIN = "WVWZZZ1KZAW557001"
THIRD_VIN = "WVWZZZ1KZAW557002"
REASON = "Beim Einkauf eine Ziffer vertauscht."


def _manager(tenant_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=True,
    )


def _bought(client, db_session, *, configuration_vin: str | None = VIN) -> tuple[dict, uuid.UUID]:
    """A car bought into stock under VIN, its configuration carrying
    `configuration_vin`; returns the item read and the Betrieb."""

    token = _token(client)
    item = _create(client, db_session, token)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.vin = configuration_vin
    db_session.commit()
    response = _purchase(client, token, item, vin=VIN)
    assert response.status_code == 200, response.text
    bought = response.json()
    assert bought["vehicleId"] is not None
    return bought, db_session.get(StockItem, uuid.UUID(bought["id"])).tenant_id


def _correct(client, token: str, item: dict, new_vin: str = NEW_VIN, **headers: str):
    return client.post(
        f"/v1/inventory/stock-items/{item['id']}/vin-correction",
        json={"newVin": new_vin, "reason": REASON},
        headers=_auth(token, **{"If-Match": str(item["version"]), **headers}),
    )


def _state(db_session, item: dict) -> tuple[str | None, str, str | None]:
    """(stock item, vehicle-mdm, configuration) VINs as stored."""

    db_session.expire_all()
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    vehicle = db_session.get(VehicleMdm, stored.vehicle_id)
    configuration = db_session.get(VehicleConfiguration, stored.configuration_id)
    return stored.vin, vehicle.vin, configuration.vin


def _audit(db_session, action: str) -> list[AuditEvent]:
    db_session.expire_all()
    return list(db_session.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)))


def _events(db_session, event_type: str) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(db_session.scalars(select(OutboxMessage).where(OutboxMessage.event_type == event_type)))


# --- the correction travels to all three records -------------------------------


def test_correction_updates_stock_vehicle_and_configuration_with_three_audit_rows(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == NEW_VIN
    assert response.json()["version"] == item["version"] + 1
    assert _state(db_session, item) == (NEW_VIN, NEW_VIN, NEW_VIN)
    rows = _audit(db_session, "vin_corrected")
    assert sorted(row.entity_type for row in rows) == ["stock_item", "vehicle_configuration", "vehicle_mdm"]
    assert {row.reason for row in rows} == {REASON}
    assert all(row.before["vin"] == VIN and row.after["vin"] == NEW_VIN for row in rows)
    # The configuration stays linked to the same car (its record now carries the new VIN).
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    assert str(configuration.vehicle_id) == item["vehicleId"]


def test_correction_publishes_stock_vehicle_and_summary_events(client, db_session):
    item, tenant_id = _bought(client, db_session)

    assert _correct(client, _manager(tenant_id), item).status_code == 200

    [stock_event] = _events(db_session, "inventory.stock_item.vin_corrected")
    assert stock_event.payload == {
        "stockNumber": item["stockNumber"],
        "vehicleId": item["vehicleId"],
        "previousVin": VIN,
        "vin": NEW_VIN,
        "correctedBy": stock_event.payload["correctedBy"],
        "configurationId": item["configurationId"],
    }
    assert "reason" not in stock_event.payload
    [vehicle_event] = _events(db_session, "vehicle.vehicle_mdm.vin_corrected")
    assert (vehicle_event.payload["previousVin"], vehicle_event.payload["vin"]) == (VIN, NEW_VIN)
    assert vehicle_event.tenant_id is None
    summary = [e for e in _events(db_session, "vehicle.vehicle_mdm.summary_changed") if e.payload["vehicleId"] == item["vehicleId"]]
    assert [e.payload["changedFields"] for e in summary] == [["vin"]]
    updated = [e for e in _events(db_session, "configuration.updated") if str(e.aggregate_id) == item["configurationId"]]
    assert len(updated) == 1


def test_the_vin_is_normalised(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = _correct(client, _manager(tenant_id), item, new_vin=" wvwzzz1kzaw 557001 ")

    assert response.status_code == 200, response.text
    assert _state(db_session, item) == (NEW_VIN, NEW_VIN, NEW_VIN)


def test_a_configuration_without_a_vin_stays_without(client, db_session):
    item, tenant_id = _bought(client, db_session, configuration_vin=None)

    assert _correct(client, _manager(tenant_id), item).status_code == 200

    assert _state(db_session, item) == (NEW_VIN, NEW_VIN, None)


def test_a_configuration_already_carrying_the_new_vin_is_relinked(client, db_session):
    """The KAN-510 path: the configurator corrected the configuration
    first, so it named no car; the correction catches the other two up."""

    item, tenant_id = _bought(client, db_session)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.vin = NEW_VIN
    configuration.vehicle_id = None
    db_session.commit()

    assert _correct(client, _manager(tenant_id), item).status_code == 200

    assert _state(db_session, item) == (NEW_VIN, NEW_VIN, NEW_VIN)
    db_session.expire_all()
    assert str(db_session.get(VehicleConfiguration, configuration.id).vehicle_id) == item["vehicleId"]


# --- refusals: nothing written anywhere ------------------------------------------


def _nothing_written(db_session, item: dict) -> None:
    assert _state(db_session, item) == (VIN, VIN, VIN)
    assert _audit(db_session, "vin_corrected") == []
    assert _events(db_session, "inventory.stock_item.vin_corrected") == []
    assert _events(db_session, "vehicle.vehicle_mdm.vin_corrected") == []
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).version == item["version"]


def test_a_vin_another_stock_item_holds_is_409_and_nothing_is_written(client, db_session):
    item, tenant_id = _bought(client, db_session)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset({AccessRole.INVENTORY}),
    )
    holder = _create(client, db_session, token, vin=NEW_VIN)

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_already_in_stock"
    assert response.json()["error"]["details"]["stockNumber"] == holder["stockNumber"]
    _nothing_written(db_session, item)


def test_a_vin_naming_another_vehicle_is_409_and_the_stock_item_is_untouched(client, db_session):
    item, tenant_id = _bought(client, db_session)
    other = resolve_vehicle_mdm_by_vin(db_session, vin=NEW_VIN, actor_id=None)

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 409, response.text
    details = response.json()["error"]["details"]
    assert details["reason"] == "vin_belongs_to_other_vehicle"
    assert details["vehicleNumber"] == db_session.get(VehicleMdm, other.id).vehicle_number
    _nothing_written(db_session, item)


def test_a_configuration_naming_a_third_vin_is_refused_before_anything_is_written(client, db_session):
    item, tenant_id = _bought(client, db_session)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.vin = THIRD_VIN
    db_session.commit()

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"] == {"field": "newVin", "reason": "configuration_vin_differs"}
    assert _state(db_session, item) == (VIN, VIN, THIRD_VIN)
    assert _audit(db_session, "vin_corrected") == []


def test_the_unchanged_vin_is_422(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = _correct(client, _manager(tenant_id), item, new_vin=VIN)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_unchanged"
    _nothing_written(db_session, item)


def test_an_invalid_vin_is_422(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = _correct(client, _manager(tenant_id), item, new_vin="NOT-A-VIN")

    assert response.status_code == 422, response.text
    _nothing_written(db_session, item)


def test_a_blank_reason_is_422(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = client.post(
        f"/v1/inventory/stock-items/{item['id']}/vin-correction",
        json={"newVin": NEW_VIN, "reason": "   "},
        headers=_auth(_manager(tenant_id), **{"If-Match": str(item["version"])}),
    )

    assert response.status_code == 422, response.text
    _nothing_written(db_session, item)


def test_a_pipeline_car_without_a_vehicle_record_is_refused(client, db_session):
    token = _token(client)
    item = _create(client, db_session, token, vin=VIN)
    tenant_id = db_session.get(StockItem, uuid.UUID(item["id"])).tenant_id

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_correction_needs_vehicle"


def test_a_car_that_left_stock_is_refused(client, db_session):
    from app.core.base import utcnow

    item, tenant_id = _bought(client, db_session)
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    stored.left_stock_at = utcnow()
    db_session.commit()

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "vin_correction_left_stock"
    assert _state(db_session, item) == (VIN, VIN, VIN)


def test_a_stale_if_match_is_refused(client, db_session):
    item, tenant_id = _bought(client, db_session)

    response = _correct(client, _manager(tenant_id), {**item, "version": item["version"] - 1})

    assert response.status_code == 409, response.text
    _nothing_written(db_session, item)


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.INVENTORY])
def test_only_the_dealer_manager_may_correct(client, db_session, role):
    item, tenant_id = _bought(client, db_session)
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)), roles=frozenset({role})
    )

    assert _correct(client, token, item).status_code == 403
    _nothing_written(db_session, item)


def test_another_betriebs_stock_item_is_not_found(client, db_session):
    item, _tenant_id = _bought(client, db_session)

    assert _correct(client, _manager(uuid.uuid4()), item).status_code == 404
    _nothing_written(db_session, item)


def test_correction_replays_on_the_same_idempotency_key(client, db_session):
    item, tenant_id = _bought(client, db_session)
    token = _manager(tenant_id)

    first = _correct(client, token, item, **{"Idempotency-Key": "vin-once"})
    second = _correct(client, token, item, **{"Idempotency-Key": "vin-once"})

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(_events(db_session, "inventory.stock_item.vin_corrected")) == 1
    assert len(_audit(db_session, "vin_corrected")) == 3


# --- ADR-047: the compensation and the retry --------------------------------------


def test_a_failed_stock_write_puts_the_vehicle_and_configuration_back(client, db_session, monkeypatch):
    item, tenant_id = _bought(client, db_session)

    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item)

    assert _state(db_session, item) == (VIN, VIN, VIN)
    reverted = _audit(db_session, "vin_correction_reverted")
    assert sorted((row.entity_type, row.before["vin"], row.after["vin"]) for row in reverted) == [
        ("vehicle_configuration", NEW_VIN, VIN),
        ("vehicle_mdm", NEW_VIN, VIN),
    ]
    assert _events(db_session, "inventory.stock_item.vin_corrected") == []
    db_session.expire_all()
    assert db_session.get(StockItem, uuid.UUID(item["id"])).version == item["version"]


def test_a_failed_outbox_write_leaves_no_stock_audit_row_and_is_compensated(client, db_session, monkeypatch):
    """A failure at the event is compensated: Vehicle is put back and no
    stock audit row or event survives."""

    item, tenant_id = _bought(client, db_session)

    def _fail(*_args, **_kwargs):
        raise RuntimeError("outbox write failed")

    monkeypatch.setattr(stock_item_service, "publish", _fail)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item)

    assert _state(db_session, item) == (VIN, VIN, VIN)
    assert [row.entity_type for row in _audit(db_session, "vin_corrected")].count("stock_item") == 0
    assert _events(db_session, "inventory.stock_item.vin_corrected") == []


def test_a_stock_write_refused_at_its_flush_is_compensated_under_the_items_lock(client, db_session, monkeypatch, engine):
    """The stock row, its audit row and its event flush inside one savepoint:
    a write the database refuses there (here a value too long for its
    column) rolls back only the savepoint, so the compensation runs while
    the request still holds the item's row lock and no second correction of
    the item can slip in between. Before, the audit and outbox rows flushed
    at the commit, whose failure ended the transaction — and its lock —
    before the compensation ran."""

    from sqlalchemy import text

    item, tenant_id = _bought(client, db_session)
    real_audit = stock_item_service.record_audit_event

    def _overlong_then_audit(db, **kwargs):
        for obj in list(db.identity_map.values()):
            if isinstance(obj, StockItem):
                obj.purchase_invoice_ref = "X" * 5000
        return real_audit(db, **kwargs)

    seen: dict[str, bool] = {}
    real_compensate = stock_item_service._compensate_vin_correction

    def _compensate_and_probe_the_lock(db, **kwargs):
        with engine.connect() as other:
            try:
                other.execute(
                    text("SELECT id FROM stock_item WHERE id = :id FOR UPDATE NOWAIT"), {"id": uuid.UUID(item["id"])}
                )
                seen["held"] = False
            except Exception:
                seen["held"] = True
            other.rollback()
        return real_compensate(db, **kwargs)

    monkeypatch.setattr(stock_item_service, "record_audit_event", _overlong_then_audit)
    monkeypatch.setattr(stock_item_service, "_compensate_vin_correction", _compensate_and_probe_the_lock)

    with pytest.raises(Exception):
        _correct(client, _manager(tenant_id), item)

    assert seen == {"held": True}
    assert _state(db_session, item) == (VIN, VIN, VIN)
    assert [row.entity_type for row in _audit(db_session, "vin_corrected")].count("stock_item") == 0
    assert _events(db_session, "inventory.stock_item.vin_corrected") == []


def test_the_compensation_never_puts_back_a_record_this_correction_did_not_change(client, db_session, monkeypatch):
    """The reviewer's case: the record already carried the new VIN before
    the request (an identity PATCH, another Betrieb's correction), so
    Vehicle changed only the configuration. A failed stock write reverts that
    configuration — and leaves the record as it found it."""

    item, tenant_id = _bought(client, db_session)
    vehicle = db_session.get(VehicleMdm, uuid.UUID(item["vehicleId"]))
    vehicle.vin = NEW_VIN
    db_session.commit()

    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item)

    assert _state(db_session, item) == (VIN, NEW_VIN, VIN)
    reverted = _audit(db_session, "vin_correction_reverted")
    assert [(row.entity_type, row.before["vin"], row.after["vin"]) for row in reverted] == [
        ("vehicle_configuration", NEW_VIN, VIN)
    ]
    assert [row.entity_type for row in _audit(db_session, "vin_corrected")] == ["vehicle_configuration"]


def test_the_compensation_of_a_configuration_only_relinked_keeps_its_vin(client, db_session, monkeypatch):
    """The reviewer's KAN-510 case: the configurator had already corrected
    the configuration's VIN (it named no car), so Vehicle wrote the record and
    only re-linked the configuration. A failed stock write puts the record
    back and unlinks the configuration again — it never gives the
    configuration the old, wrong VIN."""

    item, tenant_id = _bought(client, db_session)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.vin = NEW_VIN
    configuration.vehicle_id = None
    db_session.commit()

    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item)

    assert _state(db_session, item) == (VIN, VIN, NEW_VIN)
    db_session.expire_all()
    assert db_session.get(VehicleConfiguration, configuration.id).vehicle_id is None
    reverted = {row.entity_type: row for row in _audit(db_session, "vin_correction_reverted")}
    assert (reverted["vehicle_mdm"].before["vin"], reverted["vehicle_mdm"].after["vin"]) == (NEW_VIN, VIN)
    assert reverted["vehicle_configuration"].before["vin"] == reverted["vehicle_configuration"].after["vin"] == NEW_VIN
    assert reverted["vehicle_configuration"].after["vehicleId"] is None


def test_the_compensation_of_an_unchanged_record_and_configuration_writes_nothing(client, db_session, monkeypatch):
    item, tenant_id = _bought(client, db_session, configuration_vin=None)
    vehicle = db_session.get(VehicleMdm, uuid.UUID(item["vehicleId"]))
    vehicle.vin = NEW_VIN
    db_session.commit()

    def _fail(*_args, **_kwargs):
        raise RuntimeError("stock write failed")

    monkeypatch.setattr(stock_item_service, "_write_corrected_vin", _fail)

    with pytest.raises(RuntimeError):
        _correct(client, _manager(tenant_id), item)

    db_session.expire_all()
    assert db_session.get(VehicleMdm, vehicle.id).vin == NEW_VIN
    assert _audit(db_session, "vin_correction_reverted") == []
    assert _audit(db_session, "vin_corrected") == []


def test_a_retry_after_vehicle_committed_completes_without_a_second_vehicle_write(client, db_session):
    """The stock write failed after Vehicle committed and the compensation
    failed too: Vehicle already carries the new VIN. The retry finds it so,
    writes nothing more there and completes the stock item."""

    item, tenant_id = _bought(client, db_session)
    correct_vehicle_vin(
        db_session, vehicle_id=uuid.UUID(item["vehicleId"]), old_vin=VIN, new_vin=NEW_VIN, reason=REASON,
        actor_id=None, tenant_id=tenant_id, configuration_id=uuid.UUID(item["configurationId"]),
    )

    response = _correct(client, _manager(tenant_id), item)

    assert response.status_code == 200, response.text
    assert _state(db_session, item) == (NEW_VIN, NEW_VIN, NEW_VIN)
    assert [row.entity_type for row in _audit(db_session, "vin_corrected")].count("vehicle_mdm") == 1
    assert len(_events(db_session, "vehicle.vehicle_mdm.vin_corrected")) == 1


def test_vehicle_refuses_a_record_that_no_longer_carries_the_old_vin(client, db_session):
    from app.core.errors import ConflictError

    item, tenant_id = _bought(client, db_session)

    with pytest.raises(ConflictError) as refused:
        correct_vehicle_vin(
            db_session, vehicle_id=uuid.UUID(item["vehicleId"]), old_vin=THIRD_VIN, new_vin=NEW_VIN,
            reason=REASON, actor_id=None, tenant_id=tenant_id,
        )

    assert refused.value.details["reason"] == "vehicle_vin_changed"
    assert _state(db_session, item) == (VIN, VIN, VIN)


def test_vehicle_leaves_the_callers_pending_work_alone(client, db_session):
    """Vehicle's own session: the caller's uncommitted change is neither
    committed nor rolled back by the correction."""

    item, tenant_id = _bought(client, db_session)
    stored = db_session.get(StockItem, uuid.UUID(item["id"]))
    stored.purchase_invoice_ref = "pending, never committed"

    correct_vehicle_vin(
        db_session, vehicle_id=stored.vehicle_id, old_vin=VIN, new_vin=NEW_VIN, reason=REASON,
        actor_id=None, tenant_id=tenant_id,
    )

    assert stored.purchase_invoice_ref == "pending, never committed"
    db_session.rollback()
    assert db_session.get(StockItem, stored.id).purchase_invoice_ref != "pending, never committed"
    assert db_session.get(VehicleMdm, stored.vehicle_id).vin == NEW_VIN


# --- rule 10: drift is reported ----------------------------------------------------


def _inventory_findings(db_session) -> set[tuple[str, uuid.UUID]]:
    try:
        inventory_reconciliation.run(db_session)
    except ReconciliationAlarm as alarm:
        return {(orphan.check_label, orphan.source_row_id) for orphan in alarm.orphans}
    return set()


_DIFFERS_FROM_VEHICLE = "stock_item.vin differs from its vehicle_mdm.vin"
_DIFFERS_FROM_CONFIGURATION = "stock_item.vin differs from its configuration's vin"


def test_reconciliation_reports_a_stock_vin_its_vehicle_does_not_carry(client, db_session):
    item, _tenant_id = _bought(client, db_session)
    vehicle = db_session.get(VehicleMdm, uuid.UUID(item["vehicleId"]))
    vehicle.vin = NEW_VIN
    db_session.commit()

    findings = {label for label, row in _inventory_findings(db_session) if row == uuid.UUID(item["id"])}

    assert _DIFFERS_FROM_VEHICLE in findings


def test_reconciliation_reports_a_stock_vin_its_configuration_does_not_carry(client, db_session):
    item, _tenant_id = _bought(client, db_session)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(item["configurationId"]))
    configuration.vin = NEW_VIN
    db_session.commit()

    findings = {label for label, row in _inventory_findings(db_session) if row == uuid.UUID(item["id"])}

    assert _DIFFERS_FROM_CONFIGURATION in findings
    assert _DIFFERS_FROM_VEHICLE not in findings


def test_reconciliation_leaves_a_corrected_car_and_an_unbought_one_alone(client, db_session):
    item, tenant_id = _bought(client, db_session)
    assert _correct(client, _manager(tenant_id), item).status_code == 200
    token = _token(client)
    unbought = _create(client, db_session, token, vin=THIRD_VIN)
    configuration = db_session.get(VehicleConfiguration, uuid.UUID(unbought["configurationId"]))
    configuration.vin = None
    db_session.commit()

    labels = {label for label, _row in _inventory_findings(db_session)}

    assert not labels & {_DIFFERS_FROM_VEHICLE, _DIFFERS_FROM_CONFIGURATION}


def test_a_valuation_from_before_the_correction_is_not_reported(client, db_session):
    """A valuation is never edited (ADR-066): one made before the
    correction keeps the VIN the car carried then — dated evidence, not a
    copy gone wrong. Any other VIN is still reported."""

    item, tenant_id = _bought(client, db_session)
    before = _stored_valuation(db_session, vin=VIN, vehicle_id=uuid.UUID(item["vehicleId"]))
    wrong = _stored_valuation(db_session, vin=THIRD_VIN, vehicle_id=uuid.UUID(item["vehicleId"]))
    assert _correct(client, _manager(tenant_id), item).status_code == 200

    try:
        valuation_reconciliation.run(db_session)
        reported = set()
    except ReconciliationAlarm as alarm:
        reported = {o.source_row_id for o in alarm.orphans if o.check_label == VIN_DIFFERS}

    assert before.id not in reported
    assert wrong.id in reported


def test_a_valuation_written_after_the_correction_with_the_old_vin_is_reported(client, db_session):
    item, tenant_id = _bought(client, db_session)
    assert _correct(client, _manager(tenant_id), item).status_code == 200
    later = _stored_valuation(db_session, vin=VIN, vehicle_id=uuid.UUID(item["vehicleId"]))

    try:
        valuation_reconciliation.run(db_session)
        reported = set()
    except ReconciliationAlarm as alarm:
        reported = {o.source_row_id for o in alarm.orphans if o.check_label == VIN_DIFFERS}

    assert later.id in reported

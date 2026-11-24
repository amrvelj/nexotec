"""KAN-464 PR-3 (ADR-210): what carries the world from before customers became
contacts across the rename — each proven by a run, not by reading.

- The core migration copies the renamed consumers' `processed_event` rows, so
  an event the old name processed is never processed again under the new one.
- The platform migration moves `ui_translation` rows with their keys (a
  platform edit keeps applying) and the stored grid layouts and saved views.
- The contact reconciliation reports an event published under its old name
  still pending or dead (KAN-464 PR-5's precondition for retiring the names).
- Every consumer reading a renamed payload key still reads an event published
  before the rename (`sales.customer_merged`'s is in
  test_consumer_redelivery_idempotency.py).
"""

import datetime as dt
import importlib.util
import json
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.contact.reconciliation import CHECKS
from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage, OutboxStatus
from app.core.outbox_transport import InProcessTransport
from app.core.reconciliation import StateCheck, find_rows_in_state
from app.inventory.models.stock_item import StockItem
from app.sales.services.contract import confirm_contract, create_contract
from app.sales.services.offer import create_offer
from app.sales.services.trade_in import set_trade_in
from app.worker import register_handlers
from tests.cross_context.test_consumer_redelivery_idempotency import (
    _RecordingTransport,
    _trade_in_purchased,
    _trade_in_storno,
)
from tests.cross_context.test_kan189_trade_in_without_register import (
    _SIGNED_AT,
    _TRADE_IN_VIN,
    _dealership,
    _edit,
    _message,
    _session_factory,
    _stock_consumes_the_confirmation,
    _with_buyer,
)
from tests.identapi_support import make_user

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(relative: str):
    path = _VERSIONS / relative
    spec = importlib.util.spec_from_file_location(f"kan464_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONSUMERS = _load("core/5e1c7a9d3b20_kan464_renamed_consumers_keep_their_processed_events.py")
_UI_STATE = _load("platform/9c3e5a1f7b42_kan464_contact_keys_in_stored_ui_state.py")


def _run(engine, step) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


@pytest.fixture()
def online(monkeypatch):
    """The platform migration asks alembic's environment whether it renders SQL
    offline; outside an alembic run there is none, and this run is online."""

    monkeypatch.setattr(_UI_STATE, "context", SimpleNamespace(is_offline_mode=lambda: False))


# --- processed_event (core chain) ---------------------------------------------------------------


def test_the_renamed_consumers_keep_the_events_they_processed_and_the_downgrade_gives_them_back(engine):
    old, new = _CONSUMERS._RENAMED[2]  # customer.trade_in_purchased -> contact.trade_in_purchased
    seen, both, after = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    at = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.UTC)
    with engine.begin() as conn:
        insert = text("INSERT INTO processed_event (event_id, consumer_name, processed_at) VALUES (:e, :c, :at)")
        conn.execute(insert, [{"e": seen, "c": old, "at": at}, {"e": both, "c": old, "at": at}, {"e": both, "c": new, "at": at}])

    _run(engine, _CONSUMERS.upgrade)
    with engine.connect() as conn:
        rows = set(conn.execute(text("SELECT event_id, consumer_name FROM processed_event")).all())
    assert {(seen, old), (seen, new), (both, old), (both, new)} <= rows  # copied; an existing copy left alone

    # Processed only under the new name since the deploy …
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO processed_event (event_id, consumer_name, processed_at) VALUES (:e, :c, now())"), {"e": after, "c": new})
    _run(engine, _CONSUMERS.downgrade)
    with engine.connect() as conn:
        rows = set(conn.execute(text("SELECT event_id, consumer_name FROM processed_event")).all())
    # … goes back to the old name, so the downgraded code does not process it again.
    assert {(seen, old), (both, old), (after, old)} <= rows
    assert not {name for _, name in rows if name.startswith("contact.")}


# --- ui_translation and user_preference (platform chain) ------------------------------------------


def _translation(conn, namespace: str, key: str, value: str, source: str = "platform") -> None:
    conn.execute(
        text(
            "INSERT INTO ui_translation (id, namespace, key, language, value, seed_value, source, version, created_at, updated_at)"
            " VALUES (:id, :ns, :key, 'de', :value, :value, :source, 1, now(), now())"
        ),
        {"id": uuid.uuid4(), "ns": namespace, "key": key, "value": value, "source": source},
    )


def _revision(conn) -> int:
    return conn.execute(text("SELECT version FROM ui_translation_revision WHERE scope = 'ui'")).scalar_one()


def test_a_platform_edit_moves_with_its_key_and_the_revision_is_bumped(engine, online):
    moved = ("contactDetail", "overview.fields.contactNumber")
    assert ("customerDetail", "overview.fields.customerNumber", *moved) in _UI_STATE._KEYS
    blocked = next(pair for pair in _UI_STATE._KEYS if pair[0] == "customersList")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO ui_translation_revision (scope, version, updated_at) VALUES ('ui', 7, now()) ON CONFLICT (scope) DO NOTHING"))
        before = _revision(conn)
        _translation(conn, "customerDetail", "overview.fields.customerNumber", "Kundennr. (bearbeitet)")
        _translation(conn, blocked[0], blocked[1], "alt")  # its new key exists already …
        _translation(conn, blocked[2], blocked[3], "neu", source="bundle_seed")

    _run(engine, _UI_STATE.upgrade)
    with engine.connect() as conn:
        rows = {(ns, key): value for ns, key, value in conn.execute(text("SELECT namespace, key, value FROM ui_translation"))}
        assert rows[moved] == "Kundennr. (bearbeitet)"
        assert ("customerDetail", "overview.fields.customerNumber") not in rows
        assert rows[(blocked[0], blocked[1])] == "alt"  # … so this one stays put for the import to report
        assert rows[(blocked[2], blocked[3])] == "neu"
        assert _revision(conn) == before + 1

    _run(engine, _UI_STATE.downgrade)
    with engine.connect() as conn:
        rows = {(ns, key): value for ns, key, value in conn.execute(text("SELECT namespace, key, value FROM ui_translation"))}
    assert rows[("customerDetail", "overview.fields.customerNumber")] == "Kundennr. (bearbeitet)"


def test_the_contact_lists_layout_and_saved_views_move_and_a_role_filter_value_is_kept(db_session, engine, online):
    user = make_user(db_session, tenant_id=_dealership(db_session).id)
    layout = {
        "columnOrder": ["name", "customerNumber", "customerSince", "customerTitle", "customerType"],
        "hidden": ["duplicateOfCustomerId"],
        "sort": [{"field": "customerSince", "direction": "desc"}],
    }
    views = {"views": [{"id": "v1", "name": "Kunden seit 2020", "filters": [
        {"field": "customerSince", "condition": "after", "value": "2020-01-01"},
        {"field": "roles", "condition": "is", "value": "customer"},
    ]}]}
    sales = {"columnOrder": ["number", "customerLabel"]}
    with engine.begin() as conn:
        insert = text("INSERT INTO user_preference (user_id, scope, payload, schema_version, updated_at)"
            " VALUES (:u, :s, CAST(:p AS JSON), 1, now())")
        conn.execute(insert, [
            {"u": user.id, "s": "grid:mdm.customers.list", "p": json.dumps(layout)},
            {"u": user.id, "s": "views:mdm.customers.list", "p": json.dumps(views)},
            {"u": user.id, "s": "grid:sales.deals.list", "p": json.dumps(sales)},
        ])

    _run(engine, _UI_STATE.upgrade)
    with engine.connect() as conn:
        stored = {scope: payload for scope, payload in conn.execute(text("SELECT scope, payload FROM user_preference WHERE user_id = :u"), {"u": user.id})}
    assert set(stored) == {"grid:mdm.contacts.list", "views:mdm.contacts.list", "grid:sales.deals.list"}
    assert stored["grid:mdm.contacts.list"] == {
        "columnOrder": ["name", "contactNumber", "contactSince", "contactTitle", "contactType"],
        "hidden": ["duplicateOfContactId"],
        "sort": [{"field": "contactSince", "direction": "desc"}],
    }
    filters = stored["views:mdm.contacts.list"]["views"][0]["filters"]
    assert filters == [
        {"field": "contactSince", "condition": "after", "value": "2020-01-01"},
        {"field": "roles", "condition": "is", "value": "customer"},  # the role keeps its name
    ]
    assert stored["grid:sales.deals.list"] == {"columnOrder": ["number", "contactLabel"]}

    _run(engine, _UI_STATE.downgrade)
    with engine.connect() as conn:
        back = {scope: payload for scope, payload in conn.execute(text("SELECT scope, payload FROM user_preference WHERE user_id = :u"), {"u": user.id})}
    assert back == {"grid:mdm.customers.list": layout, "views:mdm.customers.list": views, "grid:sales.deals.list": sales}


# --- the drain check ---------------------------------------------------------------------------------


def _old_name_check() -> StateCheck:
    return next(c for c in CHECKS if isinstance(c, StateCheck) and "before the rename to contacts" in c.label)


def _outbox(db: Session, event_type: str, status: OutboxStatus, age: dt.timedelta) -> uuid.UUID:
    message = OutboxMessage(
        event_type=event_type, event_version=1, occurred_at=utcnow() - age, tenant_id=uuid.uuid4(), producer="customer",
        aggregate_type="customer", aggregate_id=uuid.uuid4(), correlation_id=uuid.uuid4(), payload={}, status=status,
        created_at=utcnow() - age,
    )
    db.add(message)
    db.flush()
    return message.id


def test_an_event_under_its_old_name_still_waiting_an_hour_on_is_reported(db_session):
    hour = dt.timedelta(hours=2)
    waiting = _outbox(db_session, "customer.merged", OutboxStatus.PENDING, hour)
    dead = _outbox(db_session, "customer.vehicle_party.linked", OutboxStatus.DEAD, hour)
    delivered = _outbox(db_session, "customer.updated", OutboxStatus.PUBLISHED, hour)
    fresh = _outbox(db_session, "customer.created", OutboxStatus.PENDING, dt.timedelta(minutes=5))
    renamed = _outbox(db_session, "contact.merged", OutboxStatus.PENDING, hour)
    db_session.commit()

    found = {row_id for row_id, _ in find_rows_in_state(db_session, _old_name_check())}

    assert found == {waiting, dead}
    assert not found & {delivered, fresh, renamed}


# --- payloads published before the rename -----------------------------------------------------------------


def _deliver_once(engine, consumer_name: str, message: OutboxMessage) -> None:
    transport = _RecordingTransport()
    register_handlers(transport)
    event_type, handler = transport.registrations[consumer_name]
    assert message.event_type == event_type
    delivering = InProcessTransport(sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False))
    delivering.register(event_type, consumer_name=consumer_name, handler=handler)
    delivering.deliver(message)


def _as_before_the_rename(db: Session, message: OutboxMessage, new_key: str, old_key: str) -> OutboxMessage:
    payload = dict(message.payload)
    assert new_key in payload, f"the producer no longer writes {new_key}"
    payload[old_key] = payload.pop(new_key)
    message.payload = payload
    db.commit()
    return message


def test_a_trade_in_purchase_naming_the_customer_records_the_former_owner(db_session, engine, monkeypatch):
    message, owners = _trade_in_purchased(db_session, engine, monkeypatch)
    _as_before_the_rename(db_session, message, "tradeInContactId", "tradeInCustomerId")
    before = owners(db_session)

    _deliver_once(engine, "contact.trade_in_purchased", message)
    db_session.expire_all()

    assert owners(db_session) - before == 1


def test_a_returned_storno_saying_returned_to_customer_reopens_the_owner(db_session, engine, monkeypatch):
    message, reopened = _trade_in_storno(db_session, engine, monkeypatch)
    _as_before_the_rename(db_session, message, "returnedToContact", "returnedToCustomer")
    before = reopened(db_session)

    _deliver_once(engine, "contact.trade_in_storno", message)
    db_session.expire_all()

    assert reopened(db_session) - before == 1


def test_a_confirmation_naming_the_customer_gives_stock_the_trade_in_seller(db_session, engine):
    dealership = _dealership(db_session)
    group_id = dealership.dealer_group_id
    offer = _with_buyer(db_session, create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4()), group_id)
    contract = create_contract(db_session, tenant_id=dealership.id, offer=offer, actor_id=uuid.uuid4())
    contract = _edit(
        db_session, contract, vehicle_source="manual", vehicle_label="Volkswagen Käfer",
        manual_vehicle_condition="used", manual_base_price=Decimal("18000.00"),
    )
    contract = set_trade_in(
        db_session, deal=contract, group_id=group_id, vin=_TRADE_IN_VIN, plate=None, canton=None,
        vehicle_label="Seat Leon", contact_id=None, actor_id=uuid.uuid4(),
    )
    contract = confirm_contract(db_session, contract=contract, group_id=group_id, actor_id=uuid.uuid4(), session_factory=_session_factory(engine))
    confirmed = _message(db_session, "sales.contract.confirmed", contract.id)
    confirmed.payload = {**confirmed.payload, "signedAt": _SIGNED_AT.isoformat()}
    _as_before_the_rename(db_session, confirmed, "contactId", "customerId")

    _stock_consumes_the_confirmation(db_session, contract)

    item = db_session.scalars(select(StockItem).where(StockItem.pipeline_ref == f"contract:{contract.id}:trade_in")).one()
    assert item.trade_in_contact_id == contract.contact_id
    assert db_session.scalar(select(func.count()).select_from(VehicleParty).where(VehicleParty.role == VehiclePartyRole.OWNER)) == 0

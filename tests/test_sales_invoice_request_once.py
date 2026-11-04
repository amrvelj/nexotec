"""KAN-539 — «Rechnung anfordern» once per contract (Anto, 2026-11-03, option A).

Before the fix `request_invoice` stored nothing on the contract: a second
request (another tab, a stale screen, the button still offered) passed every
gate and published a second `sales.contract.invoice_requested`, which
Finance's invoice leg (WP-9) would read as a second invoice trigger for one
sale. Now the request stamps `invoice_requested_at` with its outbox row, and
a second one is refused (409 `invoice_already_requested`) before anything is
written. Postgres lane (ADR-011).
"""

import datetime as dt
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, update

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
from app.sales.models.contract import SalesContract
from tests.test_sales_idempotency import _dealership, _invoiceable_contract, _token


def _invoice_events(db_session, contract_id: uuid.UUID) -> int:
    db_session.expire_all()
    return db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(
            OutboxMessage.event_type == "sales.contract.invoice_requested",
            OutboxMessage.aggregate_id == contract_id,
        )
    )


def test_a_second_invoice_request_is_refused_and_publishes_nothing(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    headers = {"Authorization": f"Bearer {_token(dealership.id, group_id)}"}
    path = f"/v1/sales/contracts/{contract.id}/request-invoice"

    first = client.post(path, headers={**headers, "If-Match": str(contract.version)})
    assert first.status_code == 200, first.text
    requested = first.json()
    assert requested["invoiceRequestedAt"] is not None
    assert requested["status"] == "confirmed"  # INVOICED stays Finance's own trigger
    assert requested["version"] == contract.version + 1

    # The current version, so the refusal is the rule, not a stale If-Match.
    second = client.post(path, headers={**headers, "If-Match": str(requested["version"])})

    assert second.status_code == 409, second.text
    details = second.json()["error"]["details"]
    assert details["reason"] == "invoice_already_requested"
    assert dt.datetime.fromisoformat(details["invoiceRequestedAt"]) == dt.datetime.fromisoformat(
        requested["invoiceRequestedAt"]
    )
    assert _invoice_events(db_session, contract.id) == 1
    audited = db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.entity_id == contract.id, AuditEvent.action == "invoice_requested")
    )
    assert audited == 1


def test_the_contract_read_names_when_invoicing_was_requested(client, db_session, engine):
    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    contract = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    headers = {"Authorization": f"Bearer {_token(dealership.id, group_id)}"}

    before = client.get(f"/v1/sales/contracts/{contract.id}", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["invoiceRequestedAt"] is None

    requested = client.post(
        f"/v1/sales/contracts/{contract.id}/request-invoice",
        headers={**headers, "If-Match": str(contract.version)},
    )
    assert requested.status_code == 200, requested.text

    after = client.get(f"/v1/sales/contracts/{contract.id}", headers=headers)
    assert after.json()["invoiceRequestedAt"] == requested.json()["invoiceRequestedAt"]


def _load_migration():
    path = next(Path(__file__).resolve().parents[1].glob("alembic/versions/sales/*_kan539_contract_invoice_requested_at.py"))
    spec = importlib.util.spec_from_file_location("kan539_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    not os.environ.get("DMS_TEST_DATABASE_URL"),
    reason="Migration backfill: migrations target Postgres only (ADR-011).",
)
def test_the_migration_stamps_contracts_requested_before_it_from_the_event_log(client, db_session, engine):
    """A request before KAN-523 wrote no audit row, only the outbox event;
    one since writes both. Either way the backfill stamps the earliest, so a
    contract requested before the migration is refused a second request."""

    dealership = _dealership(db_session)
    group_id = uuid.uuid4()
    pre_kan523 = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    since_kan523 = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    never = _invoiceable_contract(db_session, engine, dealership.id, group_id)
    headers = {"Authorization": f"Bearer {_token(dealership.id, group_id)}"}
    for contract in (pre_kan523, since_kan523):
        response = client.post(
            f"/v1/sales/contracts/{contract.id}/request-invoice",
            headers={**headers, "If-Match": str(contract.version)},
        )
        assert response.status_code == 200, response.text
    # As the database stood before the migration: no stamps, and the older
    # request without its audit row.
    db_session.execute(delete(AuditEvent).where(AuditEvent.entity_id == pre_kan523.id))
    db_session.execute(update(SalesContract).values(invoice_requested_at=None))
    db_session.commit()
    first_event = db_session.scalar(
        select(OutboxMessage.occurred_at).where(
            OutboxMessage.event_type == "sales.contract.invoice_requested", OutboxMessage.aggregate_id == pre_kan523.id
        )
    )

    with engine.begin() as conn:
        assert _load_migration().backfill(conn) == 2
        assert _load_migration().backfill(conn) == 2  # re-runnable

    db_session.expire_all()
    assert db_session.get(SalesContract, pre_kan523.id).invoice_requested_at == first_event
    assert db_session.get(SalesContract, since_kan523.id).invoice_requested_at is not None
    assert db_session.get(SalesContract, never.id).invoice_requested_at is None
    refused = client.post(
        f"/v1/sales/contracts/{pre_kan523.id}/request-invoice",
        headers={**headers, "If-Match": str(db_session.get(SalesContract, pre_kan523.id).version)},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["reason"] == "invoice_already_requested"

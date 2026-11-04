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
import uuid

from sqlalchemy import func, select

from app.core.audit_model import AuditEvent
from app.core.outbox_model import OutboxMessage
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

"""KAN-147 (Anto, 2026-10-10; ADR-050): a contract migrated from the legacy
system was invoiced there, so it is never handed to invoicing again and never
cancelled — refused by its provenance (`legacy_transaction_id`), reason
`legacy_sale`.

Ported by KAN-79 from `tests/test_migrate_transaction_rows.py`, which went
with the import (ADR-091 expired). Only that import wrote the marker, so no new
legacy contract arises; the refusal stays for the ones an environment already
holds. The contract is marked here the way the import left it: CONFIRMED, its
`legacy_transaction_id` set.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.uuid7 import uuid7
from app.sales.models.contract import ContractStatus, SalesContract
from app.sales.services.contract import cancel_contract, request_invoice
from tests.test_sales_invoiceable_replica import _contract_on, _dealership, _stock_item


def _legacy_contract(db_session) -> SalesContract:
    dealership = _dealership(db_session)
    item = _stock_item(db_session, dealership.id)
    contract = _contract_on(db_session, dealership.id, dealership.dealer_group_id, item)
    contract.status = ContractStatus.CONFIRMED
    contract.legacy_transaction_id = uuid7()
    db_session.commit()
    return contract


def _outbox_for(db_session, contract: SalesContract) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == contract.id,
                OutboxMessage.event_type.in_(("sales.contract.invoice_requested", "sales.contract.cancelled")),
            )
        ).all()
    )


def test_a_legacy_sale_cannot_be_handed_to_invoicing_and_is_refused_as_legacy_not_as_unpurchased(db_session):
    contract = _legacy_contract(db_session)
    assert contract.is_invoiceable is False  # the provenance check runs before the purchase gate

    with pytest.raises(ConflictError) as excinfo:
        request_invoice(db_session, contract=contract, actor_id=uuid.uuid4(), group_id=uuid.uuid4())

    assert excinfo.value.details == {"reason": "legacy_sale"}
    assert _outbox_for(db_session, contract) == []


def test_a_legacy_sale_cannot_be_cancelled(db_session):
    contract = _legacy_contract(db_session)

    with pytest.raises(ConflictError) as excinfo:
        cancel_contract(db_session, contract=contract, reason="test", actor_id=uuid.uuid4())

    assert excinfo.value.details == {"reason": "legacy_sale"}
    db_session.expire_all()
    assert db_session.get(SalesContract, contract.id).status == ContractStatus.CONFIRMED
    assert _outbox_for(db_session, contract) == []

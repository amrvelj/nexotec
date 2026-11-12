"""KAN-631 — a test that writes a `SalesContract` row directly, standing in
for what Sales' services write, also has valuation told what Sales' own
calls would tell it: `valuation.public`'s carry calls, each on a session of
its own (ADR-047), exactly as `sales/services/valuation_carry.py` makes them.
Valuation answers «carried by a pending / signed contract» from those rows
only (ADR-131) — a contract written behind its back is not seen, which is
what the nightly reconciliation reports."""

from sqlalchemy.orm import Session, sessionmaker

from app.sales.models.contract import ContractStatus, SalesContract
from app.valuation.public import (
    record_contract_carry_pending,
    record_contract_carry_signed,
    release_contract_carry,
)


def tell_valuation(db_session: Session, contract: SalesContract) -> None:
    """Pending for a pending contract, signed for a signed one (`signed_at`
    set, cancelled since included), released for one cancelled unsigned —
    the state Sales' calls leave valuation in for `contract` as it stands."""

    if contract.trade_in_valuation_id is None:
        return
    session = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)()
    ids = {
        "tenant_id": contract.tenant_id,
        "valuation_id": contract.trade_in_valuation_id,
        "contract_id": contract.id,
    }
    try:
        record_contract_carry_pending(session, **ids, contract_label=contract.contract_number, actor_id=None)
        if contract.signed_at is not None:
            record_contract_carry_signed(
                session, **ids, signed_at=contract.signed_at, contract_label=contract.contract_number, actor_id=None
            )
        elif contract.status != ContractStatus.PENDING:
            release_contract_carry(session, **ids, actor_id=None)
    finally:
        session.close()

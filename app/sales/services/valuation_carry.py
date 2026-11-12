"""Telling valuation which contracts carry its valuations (KAN-631, ADR-131).

Valuation sits below Sales and may not ask Sales back, so it keeps its own
«carried by a pending / signed contract» rows, written by these calls into
`valuation.public` — each on a session of its own, with its own commit
(ADR-047 Pattern B), never inside the Sales transaction:

- **pending, before the Sales commit** that makes a pending contract carry a
  valuation (a contract from an offer, a trade-in valuation attached or
  auto-attached). If that commit then fails, the row this call made pending
  is released again (the compensating action). Recorded first so that
  «Verwendung aufheben» (KAN-520) never sees a pending contract without its
  carry: a missed release afterwards only keeps the clear refused.
- **signed, after the signing commits**; **released, after a pending
  contract's cancel or the replacement of its valuation commits**. A failure
  there is logged and left to the nightly reconciliation, which reports a
  carry that disagrees with Sales (rule 10); the Sales write stands.

Only contracts are recorded: an offer never stamps or blocks a valuation.

`trade_in_valuation_carries` is the read the one-time backfill
(`scripts/backfill_valuation_contract_carry.py`) takes Sales' side from:
plain data, never a row.
"""

import datetime as dt
import logging
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.sales.models.contract import ContractStatus, SalesContract
from app.valuation.public import (
    record_contract_carry_pending,
    record_contract_carry_signed,
    release_contract_carry,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def own_session_factory(db: Session, session_factory: SessionFactory | None) -> SessionFactory:
    """The caller's factory, else sessions on the request's own engine —
    separate connections and transactions, never the request's session."""

    if session_factory is not None:
        return session_factory
    return sessionmaker(bind=db.get_bind(), autoflush=False, autocommit=False, expire_on_commit=False)


def _release(
    session_factory: SessionFactory,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
) -> None:
    session = session_factory()
    try:
        release_contract_carry(
            session, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id, actor_id=actor_id
        )
    except Exception:
        logger.exception(
            "valuation_contract_carry_failed",
            extra={"contractId": str(contract_id), "valuationId": str(valuation_id), "action": action},
        )
    finally:
        session.close()


def commit_carrying(
    db: Session,
    *,
    contract: SalesContract,
    previous_valuation_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    session_factory: SessionFactory | None = None,
) -> None:
    """Commits `db` — a write after which the pending `contract` carries
    `contract.trade_in_valuation_id` — with valuation told before and after
    (module docstring). `previous_valuation_id` is what the contract carried
    before this write; it is released once the write has committed, when it
    is another one. A refusal of the pending call refuses the write: it
    propagates before anything of it commits."""

    factory = own_session_factory(db, session_factory)
    # Plain values: after a failed commit the contract's attributes expire.
    tenant_id, contract_id, contract_label = contract.tenant_id, contract.id, contract.contract_number
    valuation_id = contract.trade_in_valuation_id
    made_pending = False
    if valuation_id is not None and contract.status == ContractStatus.PENDING:
        session = factory()
        try:
            made_pending = record_contract_carry_pending(
                session, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
                contract_label=contract_label, actor_id=actor_id,
            )
        finally:
            session.close()
    try:
        db.commit()
    except Exception:
        db.rollback()
        if made_pending and valuation_id is not None:
            _release(
                factory, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
                actor_id=actor_id, action="compensate_pending",
            )
        raise
    if previous_valuation_id is not None and previous_valuation_id != valuation_id:
        _release(
            factory, tenant_id=tenant_id, valuation_id=previous_valuation_id, contract_id=contract_id,
            actor_id=actor_id, action="release_replaced",
        )


def record_signed_after_commit(
    session_factory: SessionFactory,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str,
    signed_at: dt.datetime,
    actor_id: uuid.UUID | None,
) -> None:
    """After the signing committed: the contract is signed on the valuation."""

    session = session_factory()
    try:
        record_contract_carry_signed(
            session, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
            signed_at=signed_at, contract_label=contract_label, actor_id=actor_id,
        )
    except Exception:
        logger.exception(
            "valuation_contract_carry_failed",
            extra={"contractId": str(contract_id), "valuationId": str(valuation_id), "action": "record_signed"},
        )
    finally:
        session.close()


def release_after_cancel(
    session_factory: SessionFactory,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> None:
    """After a pending contract's cancel committed: it carries the valuation
    no more. A signed contract's cancel never calls this (ADR-066)."""

    _release(
        session_factory, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        actor_id=actor_id, action="release_cancelled",
    )


@dataclass(frozen=True)
class ContractValuationCarry:
    """What one contract says about its trade-in valuation: `carry` is
    "signed" once it was signed on it (`signed_at` set, cancelled since
    included — ADR-066), "pending" while it is pending, "released" otherwise
    (cancelled unsigned). `valuation_id` is None for a contract that carries
    none."""

    contract_id: uuid.UUID
    tenant_id: uuid.UUID
    contract_number: str
    valuation_id: uuid.UUID | None
    carry: str
    signed_at: dt.datetime | None


def trade_in_valuation_carries(
    db: Session, *, contract_ids: Collection[uuid.UUID] | None = None
) -> list[ContractValuationCarry]:
    """Every contract carrying a trade-in valuation, or — with
    `contract_ids` — those contracts whatever they carry. A SELECT only."""

    stmt = select(
        SalesContract.id,
        SalesContract.tenant_id,
        SalesContract.contract_number,
        SalesContract.trade_in_valuation_id,
        SalesContract.status,
        SalesContract.signed_at,
    ).order_by(SalesContract.id)
    if contract_ids is None:
        stmt = stmt.where(SalesContract.trade_in_valuation_id.is_not(None))
    else:
        if not contract_ids:
            return []
        stmt = stmt.where(SalesContract.id.in_(list(contract_ids)))
    return [
        ContractValuationCarry(
            contract_id=contract_id,
            tenant_id=tenant_id,
            contract_number=number,
            valuation_id=valuation_id,
            carry="signed" if signed_at is not None else "pending" if status == ContractStatus.PENDING else "released",
            signed_at=signed_at,
        )
        for contract_id, tenant_id, number, valuation_id, status, signed_at in db.execute(stmt).all()
    ]

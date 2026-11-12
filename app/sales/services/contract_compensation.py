"""A confirmation's compensating actions (ADR-047), split from contract.py
so that file stays within the source-size ratchet (KAN-617): when a
confirmation fails after it reserved the car or stamped the trade-in
valuation, each step is undone on a session of its own, under the
contract's row lock.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.public import release
from app.sales.models.contract import SalesContract
from app.valuation.public import revert_valuation_use

# The same logger contract.py logs under, so the compensation log lines keep their name.
logger = logging.getLogger("app.sales.services.contract")


@dataclass(frozen=True)
class _ConfirmationIds:
    tenant_id: uuid.UUID
    contract_id: uuid.UUID
    valuation_id: uuid.UUID | None


def _compensate_confirmation(
    ids: _ConfirmationIds,
    *,
    reservation_id: uuid.UUID | None,
    valuation_newly_used: bool,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session],
) -> None:
    """ADR-047's compensating actions for a confirmation that did not
    complete: release the reservation, and revert the trade-in valuation's
    "used" when this confirmation set it and no other signed contract
    carries it (a signed contract stays signed on it even if cancelled —
    ADR-066). Each on its own short-lived session, like the calls they undo.

    Called from an `except` block, which re-raises the original error: each
    action is attempted even if the other fails, and a failure here is
    logged rather than raised, so it never masks why the confirmation
    failed. ADR-047 leaves what stays undone to the nightly run: a revert
    that stays undone is reported by the valuation job's "used valuation
    with no signed contract carrying it" (KAN-115); a release that stays
    undone is freed by Stock's orphan sweep (KAN-122) on its first run once
    the reservation is an hour old.

    KAN-192: by the time this runs, the confirmation's own transaction has
    ended, and with it the contract's row lock. A second confirmation of the
    same contract may have taken the lock meanwhile and committed — with
    the very reservation this one made (reserve_for_contract is idempotent
    by item and contract) and the valuation already stamped. So both
    actions run under the contract's row lock, taken here on a session of
    its own, and neither runs once the contract is signed: what a committed
    confirmation points at is never undone.
    """

    guard = session_factory()
    try:
        try:
            signed_at = guard.scalar(
                select(SalesContract.signed_at)
                .where(SalesContract.tenant_id == ids.tenant_id, SalesContract.id == ids.contract_id)
                .with_for_update()
            )
        except Exception:
            # Without the lock this cannot tell whether a concurrent
            # confirmation committed, so it undoes nothing: the orphan sweep
            # and the nightly report pick up what stays done (as above).
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "lock_contract"},
            )
            return
        if signed_at is not None:
            logger.warning(
                "contract_confirm_compensation_skipped",
                extra={"contractId": str(ids.contract_id), "reason": "confirmed_concurrently"},
            )
            return
        try:
            _run_compensating_actions(
                ids,
                reservation_id=reservation_id,
                valuation_newly_used=valuation_newly_used,
                actor_id=actor_id,
                session_factory=session_factory,
            )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "compensate"},
            )
    finally:
        guard.close()


def _run_compensating_actions(
    ids: _ConfirmationIds,
    *,
    reservation_id: uuid.UUID | None,
    valuation_newly_used: bool,
    actor_id: uuid.UUID,
    session_factory: Callable[[], Session],
) -> None:
    if reservation_id is not None:
        compensating = session_factory()
        try:
            release(
                compensating,
                tenant_id=ids.tenant_id,
                reservation_id=reservation_id,
                # Per reservation, not per contract: a retried confirmation
                # makes a new reservation, and its own compensation must not
                # collide with an earlier attempt's key (KAN-114).
                idempotency_key=f"sales.contract.confirm-compensate:{ids.contract_id}:{reservation_id}",
            )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "release_reservation"},
            )
        finally:
            compensating.close()

    if valuation_newly_used and ids.valuation_id is not None:
        compensating = session_factory()
        try:
            signed_elsewhere = compensating.scalar(
                select(SalesContract.id)
                .where(
                    SalesContract.tenant_id == ids.tenant_id,
                    SalesContract.trade_in_valuation_id == ids.valuation_id,
                    SalesContract.id != ids.contract_id,
                    SalesContract.signed_at.is_not(None),
                )
                .limit(1)
            )
            if signed_elsewhere is None:
                revert_valuation_use(
                    compensating, tenant_id=ids.tenant_id, valuation_id=ids.valuation_id, actor_id=actor_id
                )
        except Exception:
            logger.exception(
                "contract_confirm_compensation_failed",
                extra={"contractId": str(ids.contract_id), "action": "revert_valuation_use"},
            )
        finally:
            compensating.close()

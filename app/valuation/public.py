"""The only surface other contexts may import from valuation. Import-
linter's contract allows `app.<other-context>` to import
`app.valuation.public`, never `app.valuation.models` /
`app.valuation.services` / `app.valuation.api` directly.

`get_valuation_or_404`/`list_valid_valuations_for_vehicle`/
`list_valid_valuations_for_vin` (KAN-189: a trade-in car not in the
register is matched by its VIN) are read
surfaces for Sales's trade-in container (PR-5: "an existing valid
valuation is offered before a new one is made") and for inventory, which
copies the pointer onto a trade-in's pipeline stock item (KAN-101).

`consume_valuation_for_contract` is a cross-context WRITE, with
`revert_valuation_use` as its compensating action — Pattern B (ADR-047),
each with its own commit — both called by Sales's `confirm_contract`
(KAN-101), never by anyone reaching into app.valuation.services directly.

`record_contract_carry_pending` / `record_contract_carry_signed` /
`release_contract_carry` (KAN-631, ADR-131) are how Sales tells valuation
which contracts carry a valuation, so valuation answers «is it carried by a
pending or a signed contract» from its own rows instead of asking Sales
back. Each commits on the session Sales opened for it (Pattern B). Pending
is recorded before the Sales write that makes a contract carry it commits
(released again by Sales if that write fails); signed and released after
the Sales commit that signs or cancels the contract, or replaces its
valuation.

`referenced_configuration_ids` (KAN-270) is vehicle's read for its daily purge of
configurations no host ever took: which of the ids it names a valuation
holds. A SELECT only.

No mapped class leaves through this door (KAN-123 step 2, ADR-162): the reads
answer a frozen `ValuationSummary` (`services/valuation_refs.py`), and what
Sales' and Stock's nightly reconciliation check against valuation's table —
an orphan reference, another dealership's valuation, an unstamped one, a
copied figure that differs — is built here by `valuation_reference_check`,
`valuation_exists`, `valuation_not_used` and `valuation_final_offer_differs`
(`services/reconciliation_targets.py`), confined to the reconciliation
modules. `ValuationSource` is an enum, not a row.
"""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.valuation.models.valuation import ValuationSource
from app.valuation.services.configuration_reference import referenced_configuration_ids
from app.valuation.services.contract_carry import record_pending as _record_pending
from app.valuation.services.contract_carry import record_released as _record_released
from app.valuation.services.contract_carry import record_signed as _record_signed
from app.valuation.services.data_export import ValuationExporter
from app.valuation.services.reconciliation_targets import (
    valuation_exists,
    valuation_final_offer_differs,
    valuation_not_used,
    valuation_reference_check,
)
from app.valuation.services.valuation import consume_for_contract as _consume_for_contract
from app.valuation.services.valuation import derive_status
from app.valuation.services.valuation import get_valuation_or_404 as _get_valuation_row_or_404
from app.valuation.services.valuation import revert_use as _revert_use
from app.valuation.services.valuation_refs import (
    ValuationSummary,
    get_valuation_or_404,
    list_valid_valuations_for_vehicle,
    list_valid_valuations_for_vin,
    summarise,
)


def consume_valuation_for_contract(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str | None,
    actor_id: uuid.UUID | None,
) -> bool:
    """Marks the valuation used unless it already is; True when this call
    set it. Raises ConflictError (`details.reason == "valuation_expired"`)
    for a valuation past its validity. Records `contract_id` as carrying it
    (pending, KAN-631) in the same commit. Own commit."""

    valuation = _get_valuation_row_or_404(db, tenant_id, valuation_id)
    return _consume_for_contract(
        db, valuation=valuation, contract_id=contract_id, contract_label=contract_label, actor_id=actor_id
    )


def record_contract_carry_pending(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    contract_label: str | None,
    actor_id: uuid.UUID | None,
) -> bool:
    """A pending contract of `tenant_id` carries the valuation. True when
    this call made the row pending, so a Sales write that then fails knows
    the `release_contract_carry` is its to make. Own commit."""

    return _record_pending(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        contract_label=contract_label, actor_id=actor_id,
    )


def record_contract_carry_signed(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    valuation_id: uuid.UUID,
    contract_id: uuid.UUID,
    signed_at: dt.datetime,
    contract_label: str | None,
    actor_id: uuid.UUID | None,
) -> None:
    """The contract was signed carrying the valuation — for good, cancelled
    later or not (ADR-066). Own commit."""

    _record_signed(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id,
        signed_at=signed_at, contract_label=contract_label, actor_id=actor_id,
    )


def release_contract_carry(
    db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID, contract_id: uuid.UUID, actor_id: uuid.UUID | None
) -> bool:
    """The (unsigned) contract no longer carries the valuation: cancelled,
    its trade-in valuation replaced, or the write that would have made it
    carry it failed. A signed carry is never released. Own commit."""

    return _record_released(
        db, tenant_id=tenant_id, valuation_id=valuation_id, contract_id=contract_id, actor_id=actor_id
    )


def revert_valuation_use(
    db: Session, *, tenant_id: uuid.UUID, valuation_id: uuid.UUID, actor_id: uuid.UUID | None
) -> ValuationSummary:
    valuation = _get_valuation_row_or_404(db, tenant_id, valuation_id)
    return summarise(_revert_use(db, valuation=valuation, actor_id=actor_id))


__all__ = [
    "ValuationExporter",
    "ValuationSource",
    "ValuationSummary",
    "consume_valuation_for_contract",
    "derive_status",
    "get_valuation_or_404",
    "list_valid_valuations_for_vehicle",
    "list_valid_valuations_for_vin",
    "record_contract_carry_pending",
    "record_contract_carry_signed",
    "referenced_configuration_ids",
    "release_contract_carry",
    "revert_valuation_use",
    "valuation_exists",
    "valuation_final_offer_differs",
    "valuation_not_used",
    "valuation_reference_check",
]

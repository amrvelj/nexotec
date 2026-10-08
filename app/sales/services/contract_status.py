"""Contract status as plain data, for another context (KAN-122). Stock's
nightly reservation sweep (app.inventory.services.reservation_sweep) asks
which of the contracts holding its reservations are still signed.

A read: no lock, no write, nothing flushed. ADR-047 forbids Sales' read and
Stock's write sharing one transaction, so the sweep calls this on a session
of its own, closed before it releases anything. Its own module rather than
app.sales.services.contract, so `app.sales.public` does not load the
contract service (and its imports of other contexts) for every importer.
"""

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.sales.models.contract import ContractStatus, SalesContract

# Bound parameters per query: far below the driver's limit (65535), however
# many reservations a dealership holds.
_CHUNK = 1000


def get_contract_statuses(
    db: Session, *, tenant_id: uuid.UUID, contract_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, ContractStatus]:
    """The status of each of `contract_ids` that exists in `tenant_id`.
    A contract that does not exist there is absent from the answer, never
    an error: for the caller, missing is a fact like any status.
    """

    ids = list(dict.fromkeys(contract_ids))
    statuses: dict[uuid.UUID, ContractStatus] = {}
    for start in range(0, len(ids), _CHUNK):
        rows = db.execute(
            select(SalesContract.id, SalesContract.status).where(
                SalesContract.tenant_id == tenant_id, SalesContract.id.in_(ids[start : start + _CHUNK])
            )
        ).all()
        statuses.update({row.id: row.status for row in rows})
    return statuses

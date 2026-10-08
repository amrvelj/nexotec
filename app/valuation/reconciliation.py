"""Valuation's outbound cross-context references (WP-8 PR-5), and (KAN-115)
the «Verwendet» stamp against the signed contracts behind it. Everything
here is read-only — see app.core.reconciliation for the mechanism.
"""

import datetime as dt

from sqlalchemy import Exists, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.customer.public import Customer
from app.platform.public import Dealership
from app.sales.public import SalesContract
from app.valuation.models.valuation import Valuation
from app.vehicle.public import VehicleConfiguration, VehicleMdm

CONTEXT = "valuation"

# A confirmation stamps its trade-in valuation on its own commit, before the
# contract is signed in the next (KAN-101, ADR-047); a stamp still without a
# signed contract after this long is not a confirmation in progress.
_CONFIRMATION_GRACE = dt.timedelta(hours=1)


def _carried_by_a_signed_contract() -> Exists:
    """A signed contract of the valuation's own dealership carries it as its
    trade-in; signed is `signed_at` set, cancelled since included (ADR-066)."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.tenant_id == Valuation.tenant_id,
            SalesContract.trade_in_valuation_id == Valuation.id,
            SalesContract.signed_at.is_not(None),
        )
        .exists()
    )


CHECKS: list[ReferenceCheck | StateCheck] = [
    ReferenceCheck(
        label="valuation.tenant_id -> dealership.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    ReferenceCheck(
        label="valuation.vehicle_id -> vehicle_mdm.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.vehicle_id,
        target_model=VehicleMdm,
        target_id_column=VehicleMdm.id,
        nullable=True,  # creatable with no vehicle in the register (confirmed live)
    ),
    ReferenceCheck(
        label="valuation.customer_id -> customer.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.customer_id,
        target_model=Customer,
        target_id_column=Customer.id,
        nullable=True,  # "Ohne Kunde" (confirmed live filter chip)
    ),
    # C-F (KAN-10): the configuration it was built or captured in.
    ReferenceCheck(
        label="valuation.configuration_id -> vehicle_configuration.id",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        source_fk_column=Valuation.configuration_id,
        target_model=VehicleConfiguration,
        target_id_column=VehicleConfiguration.id,
        nullable=True,
    ),
    StateCheck(
        # KAN-115 — only a signed deal uses a valuation (Anto, 2026-10-07).
        # A stamp no signed contract backs is a confirmation whose own commit
        # failed and whose compensating revert_valuation_use never ran or
        # failed (ADR-047 leaves that to this job), or a stamp set by hand
        # before KAN-115 confined the hand stamp to signed deals.
        label="used valuation with no signed contract carrying it",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        where=lambda: Valuation.used_at.is_not(None)
        & (Valuation.used_at < utcnow() - _CONFIRMATION_GRACE)
        & ~_carried_by_a_signed_contract(),
    ),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

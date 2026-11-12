"""Valuation's outbound cross-context references (WP-8 PR-5), (KAN-115)
the «Verwendet» stamp against the signed contracts behind it, (KAN-449)
the VIN a valuation prints against the vehicle its vehicleId names, and
(KAN-631) valuation's own «carried by a pending / signed contract» rows
against the contracts in Sales.
Everything here is read-only — see app.core.reconciliation for the mechanism.
"""

import datetime as dt

from sqlalchemy import ColumnElement, Exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.reconciliation import ReconciliationRun, ReferenceCheck, StateCheck, run_reconciliation
from app.customer.public import Customer
from app.platform.public import Dealership, actor_reference_checks
from app.sales.public import ContractStatus, SalesContract
from app.valuation.models.contract_carry import ContractCarryState, ValuationContractCarry
from app.valuation.models.valuation import Valuation, ValuationDeduction, ValuationProviderQuote
from app.valuation.services.valuation import CONFIRMATION_GRACE
from app.vehicle.public import VehicleConfiguration, VehicleMdm

CONTEXT = "valuation"

# Every Valuation table with created_by / updated_by: each must name a
# platform user, or be null (KAN-243; see
# app.platform.public.actor_reference_checks).
ACTOR_TABLES = (Valuation, ValuationDeduction, ValuationProviderQuote, ValuationContractCarry)

# A confirmation stamps its trade-in valuation on its own commit, before the
# contract is signed in the next (KAN-101, ADR-047); a stamp still without a
# signed contract after this long is not a confirmation in progress.
# «Verwendung aufheben» uses the same window (KAN-253).
_CONFIRMATION_GRACE = CONFIRMATION_GRACE
# KAN-633: how long the `valuation.created` fact may be in flight.
_OUTBOX_LAG_GRACE = dt.timedelta(hours=1)
# KAN-631: Sales tells valuation after its own commit that a contract was
# signed or cancelled; a carry and a contract both untouched this long are
# not a call in progress.
_CARRY_GRACE = dt.timedelta(hours=1)


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



def _carry_contract(*conditions: ColumnElement[bool]) -> Exists:
    """The carry row's own contract — same id, same dealership, still
    carrying the row's valuation — in the state `conditions` name."""

    return (
        select(SalesContract.id)
        .where(
            SalesContract.id == ValuationContractCarry.contract_id,
            SalesContract.tenant_id == ValuationContractCarry.tenant_id,
            SalesContract.trade_in_valuation_id == ValuationContractCarry.valuation_id,
            *conditions,
        )
        .correlate(ValuationContractCarry)
        .exists()
    )


def _carry_disagrees_with_its_contract() -> StateCheck:
    """KAN-631 (ADR-131, rule 10): valuation answers «carried by a pending /
    signed contract» from its own rows, written by Sales' calls. A row that
    says pending while its contract is not pending with this valuation
    (cancelled, signed, another valuation, never committed), signed while no
    signed contract carries it, or released while its contract still carries
    it pending or signed, is a call that failed — reported, never repaired.
    Past the grace on both sides: a call made after Sales' commit may be on
    its way."""

    def where() -> ColumnElement[bool]:
        cutoff = utcnow() - _CARRY_GRACE
        pending = _carry_contract(SalesContract.status == ContractStatus.PENDING, SalesContract.signed_at.is_(None))
        signed = _carry_contract(SalesContract.signed_at.is_not(None))
        carrying = _carry_contract(
            or_(SalesContract.status == ContractStatus.PENDING, SalesContract.signed_at.is_not(None))
        )
        recently_written = (
            select(SalesContract.id)
            .where(SalesContract.id == ValuationContractCarry.contract_id, SalesContract.updated_at >= cutoff)
            .correlate(ValuationContractCarry)
            .exists()
        )
        return (
            (ValuationContractCarry.updated_at < cutoff)
            & ~recently_written
            & or_(
                (ValuationContractCarry.state == ContractCarryState.PENDING) & ~pending,
                (ValuationContractCarry.state == ContractCarryState.SIGNED) & ~signed,
                (ValuationContractCarry.state == ContractCarryState.RELEASED) & carrying,
            )
        )

    return StateCheck(
        label="valuation_contract_carry state differs from its contract",
        source_model=ValuationContractCarry,
        source_row_id_column=ValuationContractCarry.id,
        where=where,
    )


def _carried_by_a_contract_valuation_does_not_know() -> StateCheck:
    """KAN-631: a pending or signed contract of the valuation's own
    dealership carries it, and valuation holds no carry row for that
    contract at all — the pending call never reached it (a contract written
    before the backfill, or by code that bypassed the call). A row in the
    wrong state is the check above's finding."""

    def where() -> ColumnElement[bool]:
        cutoff = utcnow() - _CARRY_GRACE
        return (
            select(SalesContract.id)
            .where(
                SalesContract.tenant_id == Valuation.tenant_id,
                SalesContract.trade_in_valuation_id == Valuation.id,
                or_(SalesContract.status == ContractStatus.PENDING, SalesContract.signed_at.is_not(None)),
                SalesContract.updated_at < cutoff,
                ~select(ValuationContractCarry.id)
                .where(
                    ValuationContractCarry.valuation_id == Valuation.id,
                    ValuationContractCarry.contract_id == SalesContract.id,
                )
                .exists(),
            )
            .correlate(Valuation)
            .exists()
        )

    return StateCheck(
        label="valuation carried by a contract it holds no carry row for",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        where=where,
    )


def _names_an_untaken_configuration() -> StateCheck:
    """KAN-633 (ADR-131, rule 10): the row names a configuration vehicle
    still counts as never taken (`first_taken_at` null) an hour after the
    row last changed — the host's `configuration_attached` fact was not
    consumed (dead-lettered, or the consumer is stuck), so vehicle's purge
    could delete it the day its safety net misses it. The hour is the
    outbox-lag grace: a fact in flight is not a finding. Vehicle names no
    host; each host reports its own rows. A configuration that no longer
    exists is the reference check's finding, not this one's."""

    return StateCheck(
        label="valuation.configuration_id names a configuration vehicle has not stamped as taken",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        where=lambda: select(VehicleConfiguration.id)
        .where(VehicleConfiguration.id == Valuation.configuration_id, VehicleConfiguration.first_taken_at.is_(None))
        .correlate(Valuation)
        .exists()
        & (Valuation.updated_at < utcnow() - _OUTBOX_LAG_GRACE),
    )


def _vin_the_car_carried_before_a_correction() -> Exists:
    """KAN-557: a valuation is never edited (ADR-066), so one made before
    «VIN korrigieren» keeps the VIN the car carried then. That is dated
    evidence, not a copy gone wrong: a VIN its record's own
    `vin_corrected` audit row names as the one before is not a finding —
    for a valuation made before that correction."""

    return (
        select(AuditEvent.id)
        .where(
            AuditEvent.entity_type == "vehicle_mdm",
            AuditEvent.entity_id == Valuation.vehicle_id,
            AuditEvent.action == "vin_corrected",
            AuditEvent.before["vin"].as_string() == func.upper(Valuation.vehicle_vin),
            # Made before that correction — a valuation written later with
            # the old VIN is a copy gone wrong and is still reported.
            Valuation.created_at <= AuditEvent.created_at,
        )
        .correlate(Valuation)
        .exists()
    )


CHECKS: list[ReferenceCheck | StateCheck] = [
    _names_an_untaken_configuration(),
    _carry_disagrees_with_its_contract(),
    _carried_by_a_contract_valuation_does_not_know(),
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
    StateCheck(
        # KAN-449: a valuation names its car twice, by vehicleId and by the
        # VIN it prints. Before KAN-449 a VIN sent beside a configuration was
        # stored as sent while vehicleId stayed the configuration's; a row
        # left that way is reported here, never repaired (the row is never
        # edited, ADR-066). A row without a VIN, or without a vehicle, names
        # its car only once and is not a finding.
        label="valuation.vehicle_vin differs from its vehicle_mdm.vin",
        source_model=Valuation,
        source_row_id_column=Valuation.id,
        where=lambda: select(VehicleMdm.id)
        .where(
            VehicleMdm.id == Valuation.vehicle_id,
            Valuation.vehicle_vin.is_not(None),
            # vehicle-mdm stores a VIN upper-cased; an old valuation row may
            # not (FR-V-15), and a spelling is not a different car. (17
            # characters leave no room for a space in a VIN.)
            VehicleMdm.vin != func.upper(Valuation.vehicle_vin),
        )
        .exists()
        & ~_vin_the_car_carried_before_a_correction(),
    ),
    # KAN-552: a quote belongs to a Betrieb. Its configuration_id is not
    # checked: a quote the advisor never turned into a valuation names a
    # configuration no host took, which the purge removes after 30 days
    # (KAN-270) — that is retention, not a broken reference.
    ReferenceCheck(
        label="valuation_provider_quote.tenant_id -> dealership.id",
        source_model=ValuationProviderQuote,
        source_row_id_column=ValuationProviderQuote.id,
        source_fk_column=ValuationProviderQuote.tenant_id,
        target_model=Dealership,
        target_id_column=Dealership.id,
    ),
    *actor_reference_checks(*ACTOR_TABLES),
]


def run(db: Session) -> ReconciliationRun:
    return run_reconciliation(db, context=CONTEXT, checks=CHECKS)

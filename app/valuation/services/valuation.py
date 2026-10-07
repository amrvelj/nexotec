"""Valuation service layer (WP-8 PR-5)."""

import datetime as dt
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import get_customer_or_404
from app.sales.public import valuations_carried_by_signed_contracts
from app.valuation.models.valuation import Valuation, ValuationDeduction, ValuationNumberSequence
from app.valuation.schemas.valuation import ValuationCreate
from app.vehicle.public import CONFIGURATION_MODE_RECORD, create_or_get_vehicle_mdm, get_configuration_for_host

_EVENT_PRODUCER = "valuation"


def allocate_valuation_number(db: Session, tenant_id: uuid.UUID) -> str:
    row = db.get(ValuationNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        # First use of this key. A concurrent first caller can insert the same
        # row: its commit turns our INSERT into a UniqueViolation (KAN-70). The
        # savepoint keeps the caller's transaction alive through that, and the
        # locked re-read below then waits for and takes the winner's row.
        # Flush the caller's own pending rows first, so that the except below
        # can only ever see this counter row's INSERT.
        db.flush()
        try:
            with db.begin_nested():
                db.add(ValuationNumberSequence(tenant_id=tenant_id, next_value=1))
        except IntegrityError:
            pass  # the concurrent caller's row now exists; re-read it below
        row = db.get(ValuationNumberSequence, tenant_id, with_for_update=True)
        assert row is not None, "ValuationNumberSequence row missing after its first-use INSERT or the concurrent winner's"

    value = row.next_value
    row.next_value += 1
    db.flush()
    return f"B-{value:06d}"


def derive_status(valuation: Valuation) -> str:
    """Evaluated on read, ALWAYS — never stored, never repaired by a
    nightly job (ADR-066/FR-V-17, confirmed live: "Der Ablauf wird beim
    Lesen berechnet, nicht über Nacht nachgeführt.").
    """

    if valuation.used_at is not None:
        return "used"
    if valuation.is_draft:
        return "draft"
    if valuation.valid_until < utcnow():
        return "expired"
    return "valid"


def _resolve_customer_label(customer) -> str:
    if customer.company_name:
        return customer.company_name
    return " ".join(part for part in [customer.first_name, customer.last_name] if part) or customer.customer_number


def get_valuation_or_404(db: Session, tenant_id: uuid.UUID, valuation_id: uuid.UUID) -> Valuation:
    valuation = db.scalar(
        select(Valuation).where(Valuation.id == valuation_id, Valuation.tenant_id == tenant_id)
    )
    if valuation is None:
        raise NotFoundError(f"Valuation {valuation_id} was not found.")
    return valuation


def get_deductions(db: Session, valuation_id: uuid.UUID) -> list[ValuationDeduction]:
    return list(
        db.scalars(
            select(ValuationDeduction)
            .where(ValuationDeduction.valuation_id == valuation_id)
            .order_by(ValuationDeduction.position)
        ).all()
    )


def create_valuation(
    db: Session, *, tenant_id: uuid.UUID, group_id: uuid.UUID, data: ValuationCreate, actor_id: uuid.UUID | None
) -> Valuation:
    """Creatable with no customer, no offer, no vehicle in the register
    (confirmed live). Without a configuration, a `vin` resolves or creates
    the real vehicle-mdm record in the SAME step (FR-V's own "one step, not
    two"). With one (C-F, FR-C-14), nothing is written to vehicle-mdm.
    """

    configuration = None
    if data.configuration_id is not None:
        # FR-C-14 (amends FR-V-17, ADR-070): a valuation captured in the
        # configurator creates a configuration, not a vehicle — the vehicle
        # fields default from it, and only an MDM record the configuration
        # already links is referenced. `record` mode only (PRD v1.4).
        configuration = get_configuration_for_host(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
        configuration.require_mode(host="valuation", allowed=(CONFIGURATION_MODE_RECORD,))

    vehicle_id = None
    if configuration is not None:
        vehicle_id = configuration.vehicle_id
    elif data.vin:
        vehicle, _created = create_or_get_vehicle_mdm(db, vin=data.vin)
        vehicle_id = vehicle.id

    def _from_configuration(value, attribute: str):
        if value is not None or configuration is None:
            return value
        return getattr(configuration, attribute)

    customer_label = None
    if data.customer_id is not None:
        customer = get_customer_or_404(db, group_id, data.customer_id)
        customer_label = _resolve_customer_label(customer)

    valid_from = utcnow().date()
    valid_until = utcnow() + dt.timedelta(days=data.valid_for_days)

    valuation = Valuation(
        tenant_id=tenant_id,
        valuation_number=allocate_valuation_number(db, tenant_id),
        vehicle_id=vehicle_id,
        vehicle_make=_from_configuration(data.vehicle_make, "brand_display_name"),
        vehicle_model=_from_configuration(data.vehicle_model, "model_group_name"),
        vehicle_trim=_from_configuration(data.vehicle_trim, "variant_name"),
        vehicle_plate=_from_configuration(data.vehicle_plate, "licence_plate"),
        vehicle_vin=_from_configuration(data.vin, "vin"),
        vehicle_first_registration=_from_configuration(data.vehicle_first_registration, "first_registration_date"),
        mileage=_from_configuration(data.mileage, "mileage_km"),
        configuration_id=configuration.id if configuration is not None else None,
        configuration_label=configuration.label if configuration is not None else None,
        configuration_label_refreshed_at=utcnow() if configuration is not None else None,
        customer_id=data.customer_id,
        customer_label=customer_label,
        source=data.source,
        provider_value=data.provider_value,
        final_offer=data.final_offer,
        note=data.note,
        valid_from=valid_from,
        valid_until=valid_until,
        is_draft=data.is_draft,
        supersedes_valuation_id=data.supersedes_valuation_id,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(valuation)
    db.flush()

    for position, deduction in enumerate(data.deductions):
        db.add(
            ValuationDeduction(
                tenant_id=tenant_id,
                valuation_id=valuation.id,
                label=deduction.label,
                amount=deduction.amount,
                position=position,
            )
        )

    publish(
        db,
        OutboxEvent(
            event_type="valuation.created",
            tenant_id=tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="valuation",
            aggregate_id=valuation.id,
            payload={"valuationNumber": valuation.valuation_number},
        ),
    )
    db.commit()
    db.refresh(valuation)
    return valuation


def mark_used(db: Session, *, valuation: Valuation, actor_id: uuid.UUID | None) -> Valuation:
    """Stamps the valuation «Verwendet» and publishes `valuation.used` — the
    one writer of `used_at`. Its two callers decide whether the stamp is
    allowed: `consume_for_contract` (a confirmation; refuses a valuation
    past its validity) and `mark_used_by_hand` (a signed contract must carry
    it, KAN-115). Own commit.
    """

    if valuation.used_at is not None:
        return valuation  # idempotent — a contract confirmed twice via retry must not double-fire

    valuation.used_at = utcnow()
    valuation.updated_by = actor_id
    valuation.version += 1
    db.flush()
    publish(
        db,
        OutboxEvent(
            event_type="valuation.used",
            tenant_id=valuation.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="valuation",
            aggregate_id=valuation.id,
            payload={"valuationNumber": valuation.valuation_number},
        ),
    )
    db.commit()
    db.refresh(valuation)
    return valuation


def consume_for_contract(db: Session, *, valuation: Valuation, actor_id: uuid.UUID | None) -> bool:
    """KAN-101 — a contract's confirmation consumes its trade-in valuation.
    Returns True when THIS call set `used_at`, so the caller knows whether a
    compensating `revert_use` is its to make.

    One valuation may back several contracts (Anto, 2026-09-29): an
    already-used valuation is accepted and nothing is published again. A
    valuation past its validity is refused whether or not it is already
    used — the dealership no longer stands behind that figure. A draft is
    accepted, as `mark_used` accepts it.
    """

    # Row lock: two contracts confirmed at the same moment serialise here,
    # so exactly one of them sets used_at and publishes valuation.used.
    db.refresh(valuation, with_for_update=True)
    if not valuation.is_draft and valuation.valid_until < utcnow():
        raise ConflictError(
            f"Valuation {valuation.valuation_number} expired on {valuation.valid_until.date().isoformat()}.",
            details={"reason": "valuation_expired", "valuationId": str(valuation.id)},
        )
    if valuation.used_at is not None:
        return False
    mark_used(db, valuation=valuation, actor_id=actor_id)
    return True


def mark_used_by_hand(db: Session, *, valuation: Valuation, actor_id: uuid.UUID | None) -> Valuation:
    """«Als verwendet markieren» — repairs a stamp a signed deal should have
    set. Only a signed deal stamps a valuation (Anto, 2026-10-07, KAN-115),
    so this is allowed only when a signed contract of this dealership
    carries the valuation as its trade-in, whatever its status: an expired
    one included, since the contract was signed on it — typically before
    KAN-101 stamped at confirmation. Refused otherwise
    (`details.reason == "no_signed_contract"`). A valuation already stamped
    is left as it is.
    """

    # Row lock, as in consume_for_contract: a confirmation stamping the same
    # valuation at this moment serialises here, so valuation.used fires once.
    # Both early exits write nothing; the rollback only releases the lock.
    db.refresh(valuation, with_for_update=True)
    if valuation.used_at is not None:
        db.rollback()
        return valuation
    carried = valuations_carried_by_signed_contracts(db, tenant_id=valuation.tenant_id, valuation_ids=[valuation.id])
    if valuation.id not in carried:
        db.rollback()
        raise ConflictError(
            f"Valuation {valuation.valuation_number} is the trade-in of no signed contract, so it cannot be "
            f"marked used: only a signed deal uses a valuation.",
            details={"reason": "no_signed_contract", "valuationId": str(valuation.id)},
        )
    return mark_used(db, valuation=valuation, actor_id=actor_id)


def revert_use(db: Session, *, valuation: Valuation, actor_id: uuid.UUID | None) -> Valuation:
    """The compensating action for `consume_for_contract` (ADR-047) — only
    for a confirmation whose own transaction failed, so no contract ever
    consumed the valuation. Never a way to re-open a used valuation once a
    contract has been signed on it (ADR-066); the caller decides that.
    """

    if valuation.used_at is None:
        return valuation  # idempotent — a retried compensation is a no-op
    valuation.used_at = None
    valuation.updated_by = actor_id
    valuation.version += 1
    db.flush()
    publish(
        db,
        OutboxEvent(
            event_type="valuation.valuation.use_reverted",
            tenant_id=valuation.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="valuation",
            aggregate_id=valuation.id,
            payload={"valuationNumber": valuation.valuation_number},
        ),
    )
    db.commit()
    db.refresh(valuation)
    return valuation


def list_valid_valuations_for_vehicle(db: Session, *, tenant_id: uuid.UUID, vehicle_id: uuid.UUID) -> list[Valuation]:
    """FR-S-08: "an existing valid valuation is offered before making a
    new one." Newest first — the newest is current (ADR-048 as amended).
    """

    rows = db.scalars(
        select(Valuation)
        .where(Valuation.tenant_id == tenant_id, Valuation.vehicle_id == vehicle_id)
        .order_by(Valuation.created_at.desc())
    ).all()
    return [v for v in rows if derive_status(v) == "valid"]


# Chip -> SQL predicate, matching the confirmed reference prototype's own
# filter set exactly. "Läuft ab" (expiring soon)'s window is not specified
# anywhere found — a placeholder, single named constant, flagged for
# product (Open Item O-4 in the plan).
EXPIRING_SOON_WINDOW_DAYS = 14


def list_valuations(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    chip: str | None,
    q: str | None,
    created_by: uuid.UUID | None,
    params: SortPageParams,
) -> tuple[list[Valuation], str | None, int, bool]:
    stmt = select(Valuation).where(Valuation.tenant_id == tenant_id)

    now = utcnow()
    if chip == "valid":
        stmt = stmt.where(Valuation.used_at.is_(None), ~Valuation.is_draft, Valuation.valid_until >= now)
    elif chip == "expiring_soon":
        stmt = stmt.where(
            Valuation.used_at.is_(None),
            ~Valuation.is_draft,
            Valuation.valid_until >= now,
            Valuation.valid_until <= now + dt.timedelta(days=EXPIRING_SOON_WINDOW_DAYS),
        )
    elif chip == "expired":
        stmt = stmt.where(Valuation.used_at.is_(None), Valuation.valid_until < now)
    elif chip == "unattached":
        stmt = stmt.where(Valuation.customer_id.is_(None))
    elif chip == "mine":
        stmt = stmt.where(Valuation.created_by == created_by)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                Valuation.valuation_number.ilike(like),
                Valuation.vehicle_vin.ilike(like),
                Valuation.vehicle_plate.ilike(like),
                Valuation.customer_label.ilike(like),
            )
        )

    total, total_is_estimate = count_capped(db, stmt, threshold=get_settings().count_exact_threshold)
    stmt = paginate_query_sorted(stmt, model=Valuation, params=params)
    rows = list(db.scalars(stmt).all())
    items, next_cursor = build_sorted_page(rows, params)
    return items, next_cursor, total, total_is_estimate

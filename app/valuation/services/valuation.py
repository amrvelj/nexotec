"""Valuation service layer (WP-8 PR-5)."""

import datetime as dt
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.outbox import OutboxEvent, publish
from app.core.pagination import SortPageParams, build_sorted_page, count_capped, paginate_query_sorted
from app.customer.public import get_customer_or_404
from app.valuation.models.valuation import Valuation, ValuationDeduction, ValuationNumberSequence
from app.valuation.schemas.valuation import ValuationCreate
from app.vehicle.public import create_or_get_vehicle_mdm

_EVENT_PRODUCER = "valuation"


def allocate_valuation_number(db: Session, tenant_id: uuid.UUID) -> str:
    row = db.get(ValuationNumberSequence, tenant_id, with_for_update=True)
    if row is None:
        row = ValuationNumberSequence(tenant_id=tenant_id, next_value=1)
        db.add(row)
        db.flush()
        row = db.get(ValuationNumberSequence, tenant_id, with_for_update=True)
        assert row is not None, "just-flushed ValuationNumberSequence row vanished before it could be re-read"

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
    (confirmed live). A `vin` resolves or creates the real vehicle-mdm
    record in the SAME step (FR-V's own "one step, not two") — never a
    separate follow-up call the seller has to remember to make.
    """

    vehicle_id = None
    if data.vin:
        vehicle, _created = create_or_get_vehicle_mdm(db, vin=data.vin)
        vehicle_id = vehicle.id

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
        vehicle_make=data.vehicle_make,
        vehicle_model=data.vehicle_model,
        vehicle_trim=data.vehicle_trim,
        vehicle_plate=data.vehicle_plate,
        vehicle_vin=data.vin,
        vehicle_first_registration=data.vehicle_first_registration,
        mileage=data.mileage,
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
    if valuation.used_at is not None:
        return valuation  # idempotent — a contract confirmed twice via retry must not double-fire
    if derive_status(valuation) not in ("valid", "draft"):
        raise ConflictError(
            f"Valuation {valuation.valuation_number} cannot be marked used from status "
            f"'{derive_status(valuation)}'."
        )

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

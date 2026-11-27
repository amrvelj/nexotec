"""«Fahrzeug ist da» and the stay of the car (KAN-813, AS1-10; build slice 1,
section 3.4; ADR-213, Decision C; decision 61; checks 22, 23, 24).

| What happens | In state | Result | Refused when |
| --- | --- | --- | --- |
| «Fahrzeug ist da»: mileage, key tag, parking place, a remark | No running stay | A stay begins; every open order of the car at this Betrieb joins it; the mileage goes to the car's record | The mileage is not a whole number from 0 (decision 61). The car is here already (check 23). The order is frozen. |
| A second order for a car that is here | Here | The order joins the stay; nothing is received twice (decision 61) | – |

**One running stay per car and Betrieb** is held by the database
(`uq_service_stay_running_vehicle`); the per-car advisory lock taken first
makes a second reception wait and then refuse in one sentence instead of
failing on the index.

**The mileage reaches the car through an event** (check 24): the arrival
publishes `aftersales.service_stay.arrived` in its own transaction; `vehicle`
consumes it and records the reading with the source «service visit». A
lower reading than the last is taken and flagged by vehicle's own rule
(check 22); the stay keeps the last reading it saw, so the reception shows
«lower than the last» as it was then.

The departure and its one gate are KAN-817's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder
from app.aftersales.models.service_stay import ArrivalChannel, ServiceOrderStay, ServiceStay
from app.aftersales.services import service_order as orders
from app.core.base import utcnow
from app.core.concurrency import check_version
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.relative_dates import session_zone
from app.vehicle.public import current_mileage

ENTITY_TYPE = "service_stay"
# `<context>.<entity>.<verb>`, the entity named as its aggregate_type (events.md).
ARRIVED_EVENT = "aftersales.service_stay.arrived"
_PARKING_LENGTH = 100
_KEY_TAG_LENGTH = 50
_REMARK_LENGTH = 1000
# A mileage a car can show: an odometer has at most seven digits. Above it (or above the
# column's range) a typing error is refused in one sentence, never a 500.
MAX_MILEAGE = 9_999_999


@dataclass(frozen=True)
class ArrivalInput:
    mileage: int
    key_tag: str | None = None
    parking_place: str | None = None
    remark: str | None = None


def _text(value: str | None, length: int, field: str) -> str | None:
    text = (value or "").strip()
    if len(text) > length:
        raise UnprocessableEntityError(
            f"{field} has at most {length} characters.",
            details={"reason": "service_stay_text_too_long", "field": field, "maxLength": length},
        )
    return text or None


def _payload(stay: ServiceStay) -> dict:
    """One payload shape for every event of the stay (ADR-202)."""

    return {
        "stayId": str(stay.id),
        "vehicleId": str(stay.vehicle_id),
        "locationId": str(stay.location_id),
        "arrivedAt": stay.arrived_at.isoformat(),
        # The Swiss day of the arrival: the day of the odometer reading.
        "arrivedOn": stay.arrived_at.astimezone(session_zone()).date().isoformat(),
        "arrivalMileage": stay.arrival_mileage,
        "arrivedBy": str(stay.arrived_by) if stay.arrived_by else None,
        "departedAt": stay.departed_at.isoformat() if stay.departed_at else None,
    }


def _join(db: Session, *, order: ServiceOrder, stay: ServiceStay, actor_id: uuid.UUID | None) -> None:
    db.add(
        ServiceOrderStay(
            tenant_id=order.tenant_id, service_order_id=order.id, stay_id=stay.id, joined_at=utcnow(),
            created_by=actor_id, updated_by=actor_id,
        )
    )
    db.flush()


def _lock_car_and_orders(
    db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID
) -> tuple[uuid.UUID, list[ServiceOrder]]:
    """The locks of an arrival, in the order every writer takes them (KAN-837, KAN-813): the
    car's merge lock, then its stay lock, then every order of the car the stay will hold —
    the requested one included — in id order, read afresh (never a copy read before the
    wait). A merge that moved the order to another car meanwhile is followed."""

    for _ in range(3):
        vehicle_id = db.scalar(
            select(ServiceOrder.vehicle_id).where(ServiceOrder.id == order_id, ServiceOrder.tenant_id == tenant_id)
        )
        if vehicle_id is None:
            raise NotFoundError(f"Service order {order_id} was not found.")
        orders.lock_order_vehicle(db, vehicle_id)
        orders.lock_stay_car(db, tenant_id=tenant_id, vehicle_id=vehicle_id)
        held = list(
            db.scalars(
                select(ServiceOrder)
                .where(
                    ServiceOrder.tenant_id == tenant_id,
                    or_(
                        ServiceOrder.id == order_id,
                        and_(ServiceOrder.vehicle_id == vehicle_id, ServiceOrder.state.in_(orders.OPEN_STATES)),
                    ),
                )
                .order_by(ServiceOrder.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        requested = next(o for o in held if o.id == order_id)
        if requested.vehicle_id == vehicle_id:
            return vehicle_id, held
    raise ConflictError(
        "The order's car changed while it was being received — reload it and try again.",
        details={"reason": "service_stay_car_changed"},
    )


def receive_car(
    db: Session, *, tenant_id: uuid.UUID, order_id: uuid.UUID, expected_version: int, actor_id: uuid.UUID,
    data: ArrivalInput,
) -> ServiceStay:
    """D14 «Fahrzeug ist da» at the desk: takes its own locks (`_lock_car_and_orders`) and
    judges If-Match against the order as it stands after them. Commits."""

    vehicle_id, held = _lock_car_and_orders(db, tenant_id=tenant_id, order_id=order_id)
    order = next(o for o in held if o.id == order_id)
    check_version(order.version, expected_version, entity_name="Service order")
    orders.refuse_frozen(order)
    if not 0 <= data.mileage <= MAX_MILEAGE:
        raise UnprocessableEntityError(
            f"A mileage is a whole number from 0 to {MAX_MILEAGE:,}.".replace(",", "'"),
            details={"reason": "service_stay_mileage_invalid", "max": MAX_MILEAGE},
        )
    if orders.running_stay(db, tenant_id=tenant_id, vehicle_id=vehicle_id) is not None:
        raise ConflictError(
            f"{order.vehicle_label} is here already and cannot be received a second time.",
            details={"reason": "service_stay_car_already_here"},
        )
    key_tag = _text(data.key_tag, _KEY_TAG_LENGTH, "keyTag")
    parking_place = _text(data.parking_place, _PARKING_LENGTH, "parkingPlace")
    remark = _text(data.remark, _REMARK_LENGTH, "remark")
    now = utcnow()
    stay = ServiceStay(
        tenant_id=tenant_id,
        vehicle_id=vehicle_id,
        vehicle_label=order.vehicle_label,
        vehicle_label_refreshed_at=order.vehicle_label_refreshed_at,
        location_id=order.location_id,
        location_label=order.location_label,
        location_label_refreshed_at=order.location_label_refreshed_at,
        parking_place=parking_place,
        key_tag=key_tag,
        arrived_at=now,
        arrived_by=actor_id,
        arrival_channel=ArrivalChannel.DESK,
        arrival_mileage=data.mileage,
        previous_mileage=current_mileage(db, vehicle_id),
        arrival_remark=remark,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(stay)
    db.flush()
    after = {
        "stayId": str(stay.id),
        "mileage": data.mileage,
        "previousMileage": stay.previous_mileage,
        "keyTag": key_tag,
        "parkingPlace": parking_place,
        "remark": remark,
        "channel": ArrivalChannel.DESK.value,
    }
    # Every open order of the car at this Betrieb is held by the stay (decision 61, check 40).
    for held_order in held:
        _join(db, order=held_order, stay=stay, actor_id=actor_id)
        orders.record_order_change(db, held_order, action="service_order_car_arrived", actor_id=actor_id, after=after)
    publish(
        db,
        OutboxEvent(
            event_type=ARRIVED_EVENT,
            tenant_id=tenant_id,
            producer=orders.PRODUCER,
            aggregate_type=ENTITY_TYPE,
            aggregate_id=stay.id,
            payload=_payload(stay),
        ),
    )
    db.commit()
    return stay

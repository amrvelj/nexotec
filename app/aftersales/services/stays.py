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
publishes `aftersales.stay.arrived` in its own transaction; `vehicle`
consumes it and records the reading with the source «service visit». A
lower reading than the last is taken and flagged by vehicle's own rule
(check 22); the stay keeps the last reading it saw, so the reception shows
«lower than the last» as it was then.

The departure and its one gate are KAN-817's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import ServiceOrder
from app.aftersales.models.service_stay import ArrivalChannel, ServiceOrderStay, ServiceStay
from app.aftersales.services import service_order as orders
from app.core.base import utcnow
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.relative_dates import session_zone
from app.vehicle.public import current_mileage

ENTITY_TYPE = "service_stay"
ARRIVED_EVENT = "aftersales.stay.arrived"
_PARKING_LENGTH = 100
_KEY_TAG_LENGTH = 50
_REMARK_LENGTH = 1000


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


def receive_car(db: Session, *, order: ServiceOrder, actor_id: uuid.UUID, data: ArrivalInput) -> ServiceStay:
    """D14 «Fahrzeug ist da» at the desk. The caller has taken `orders.lock_stay_car` and
    then the order's row. Commits."""

    orders.refuse_frozen(order)
    if data.mileage < 0:
        raise UnprocessableEntityError(
            "A mileage is a whole number from 0.", details={"reason": "service_stay_mileage_invalid"}
        )
    if orders.running_stay(db, tenant_id=order.tenant_id, vehicle_id=order.vehicle_id) is not None:
        raise ConflictError(
            f"{order.vehicle_label} is here already and cannot be received a second time.",
            details={"reason": "service_stay_car_already_here"},
        )
    key_tag = _text(data.key_tag, _KEY_TAG_LENGTH, "keyTag")
    parking_place = _text(data.parking_place, _PARKING_LENGTH, "parkingPlace")
    remark = _text(data.remark, _REMARK_LENGTH, "remark")
    now = utcnow()
    stay = ServiceStay(
        tenant_id=order.tenant_id,
        vehicle_id=order.vehicle_id,
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
        previous_mileage=current_mileage(db, order.vehicle_id),
        arrival_remark=remark,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(stay)
    db.flush()
    # Every open order of the car at this Betrieb is held by the stay (decision 61, check 40).
    held = [order] + [
        other
        for other in db.scalars(
            select(ServiceOrder)
            .where(
                ServiceOrder.tenant_id == order.tenant_id,
                ServiceOrder.vehicle_id == order.vehicle_id,
                ServiceOrder.id != order.id,
                ServiceOrder.state.in_(orders.OPEN_STATES),
            )
            .order_by(ServiceOrder.id)
            .with_for_update()
        )
    ]
    after = {
        "stayId": str(stay.id),
        "mileage": data.mileage,
        "previousMileage": stay.previous_mileage,
        "keyTag": key_tag,
        "parkingPlace": parking_place,
        "remark": remark,
        "channel": ArrivalChannel.DESK.value,
    }
    for held_order in held:
        _join(db, order=held_order, stay=stay, actor_id=actor_id)
        orders.record_order_change(db, held_order, action="service_order_car_arrived", actor_id=actor_id, after=after)
    publish(
        db,
        OutboxEvent(
            event_type=ARRIVED_EVENT,
            tenant_id=order.tenant_id,
            producer=orders.PRODUCER,
            aggregate_type=ENTITY_TYPE,
            aggregate_id=stay.id,
            payload=_payload(stay),
        ),
    )
    db.commit()
    return stay

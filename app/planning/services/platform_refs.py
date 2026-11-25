"""The platform facts a resource copies beside an id (rule 2; ADR-221 T5):
a location's name and a person's name, each validated in the Betrieb on the
write that stores it. Read through `platform.public` only."""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.planning.models.resource import LABEL_LENGTH
from app.platform.public import get_active_user_acting_in, get_location_labels, list_active_users_acting_in


def location_label_for(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> str:
    """The name to store beside `location_id`: 422 `unknown_location` for an
    id that is not one of the Betrieb's locations (another Betrieb's says
    nothing more), 422 `planning_location_inactive` for a deactivated one."""

    current = get_location_labels(db, {location_id}).get(location_id)
    if current is None or current.dealership_id != dealership_id:
        raise UnprocessableEntityError(
            "The location is not one of this business's.",
            details={"reason": "unknown_location", "field": "locationId", "locationId": str(location_id)},
        )
    if not current.is_active:
        raise UnprocessableEntityError(
            "A deactivated location cannot hold an active planner resource.",
            details={"reason": "planning_location_inactive", "locationId": str(location_id)},
        )
    return current.label[:LABEL_LENGTH]


def person_label_for(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """The name to store beside `user_id`: the user must work in the Betrieb
    (homed there or an active member) and be active or invited — else 422
    `planning_person_not_in_dealership`, whether the user exists elsewhere or not."""

    user = get_active_user_acting_in(db, dealership_id=dealership_id, user_id=user_id)
    if user is None:
        raise UnprocessableEntityError(
            "Only someone who works in this business can be planned.",
            details={"reason": "planning_person_not_in_dealership", "userId": str(user_id)},
        )
    return user.label[:LABEL_LENGTH]


def users_acting_in(db: Session, *, dealership_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """Everyone who may be a person resource of the Betrieb, by name: id -> label."""

    return {user.id: user.label for user in list_active_users_acting_in(db, dealership_id=dealership_id)}

"""The one rule for a coded field a client writes inside `vehicle`: the value
must be an *active* value of the reference list of the same name.

Two writers use it, each passing its own fields: the legacy `Vehicle`
create / patch (KAN-57, seven fields) and the configuration's manual create
and PATCH (KAN-96, the five coded spec fields). Each field name is also its
list code — `vehicle_kind` is the PRD's own list (migration 6ba0a99ed5c4),
not the legacy `vehicle_type`. Keeping the check here, rather than a copy in
each service, is what stops the two drifting apart.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app.core.errors import UnprocessableEntityError
from app.platform.public import get_active_reference_value_codes


def validate_reference_codes(db: Session, values: Mapping[str, Any], fields: Iterable[str]) -> None:
    """A submitted value that is not an *active* value of its reference list
    is a semantic error in the request: 422, naming every offending field,
    the value sent and the list it was checked against (KAN-57 — the rule
    KAN-32 set for customer nationality). A list that does not exist at all
    is a deployment fault — the seed migration has not run — so it is a
    `RuntimeError` (500), never a 404 or 422 that reads like the client sent
    something wrong. A deactivated value is rejected on new writes; rows that
    already reference one stay readable. A field absent from `values`, or
    `None`, is not checked.
    """

    invalid: list[tuple[str, str]] = []
    for field in fields:
        value_code = values.get(field)
        if value_code is None:
            continue
        valid = get_active_reference_value_codes(db, field)
        if valid is None:
            raise RuntimeError(
                f"The '{field}' reference list is not seeded. Run `alembic upgrade heads` on this "
                f"deployment — {to_camel(field)} cannot be validated without it."
            )
        if value_code not in valid:
            invalid.append((field, value_code))
    if invalid:
        rendered = ", ".join(f"{to_camel(field)}={code!r} (reference list '{field}')" for field, code in invalid)
        raise UnprocessableEntityError(
            f"Not an active value of the matching reference list: {rendered}.",
            details={"invalid": {to_camel(field): code for field, code in invalid}},
        )

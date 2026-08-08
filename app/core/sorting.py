"""Server-side sort parameter parsing (U-02, UI/UX Core Principles §
FR-UI-01): `?sort=field:dir[,field:dir]`.

"Every column declared sortable must have a supporting index. A column
with no index is not shipped as sortable" (U-03) — that rule is enforced by
what an entity's allow-list chooses to expose, not by this module; see
app/api/v1/customers.py's CUSTOMER_SORT_FIELDS for where that judgment call
is made and which migration backs each column.
"""

import dataclasses
from typing import Any

from app.core.errors import UnprocessableEntityError

_VALID_DIRECTIONS = {"asc", "desc"}


@dataclasses.dataclass(frozen=True)
class SortField:
    api_name: str
    column: Any  # InstrumentedAttribute
    direction: str  # "asc" | "desc"
    nullable: bool


def parse_sort(sort: str | None, *, allowed: dict[str, Any]) -> list[SortField]:
    """`allowed` maps the API-facing field name (camelCase, matching the
    response schema) to the SQLAlchemy column it sorts on. Unknown fields,
    malformed specs, and repeated fields are all `422`s — never a silent
    ignore, per the doc: "Unknown fields return 422, never a silent ignore."
    """

    if not sort:
        return []

    fields: list[SortField] = []
    seen: set[str] = set()
    for raw_part in sort.split(","):
        part = raw_part.strip()
        if not part:
            continue
        name, sep, direction = part.partition(":")
        if not sep:
            raise UnprocessableEntityError(
                f"Invalid sort spec '{part}' — expected 'field:asc' or 'field:desc'.", details={"sort": part}
            )
        column = allowed.get(name)
        if column is None:
            raise UnprocessableEntityError(
                f"Unknown sort field '{name}'.", details={"field": name, "allowed": sorted(allowed)}
            )
        if direction not in _VALID_DIRECTIONS:
            raise UnprocessableEntityError(
                f"Invalid sort direction '{direction}' for field '{name}' — must be 'asc' or 'desc'.",
                details={"field": name, "direction": direction},
            )
        if name in seen:
            raise UnprocessableEntityError(
                f"Sort field '{name}' was specified more than once.", details={"field": name}
            )
        seen.add(name)
        fields.append(
            SortField(
                api_name=name,
                column=column,
                direction=direction,
                nullable=column.property.columns[0].nullable,
            )
        )
    return fields

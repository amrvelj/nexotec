"""The dealer groups the onboarding wizard offers (KAN-210, KAN-652).

Platform staff pick an existing group by typing part of its name; the list is
read one page at a time, ordered by name, with the row id as the tiebreaker —
the shared sorted keyset cursor of `app.core.pagination` — so it stays bounded
however many groups are onboarded.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BadRequestError
from app.core.pagination import SortCursorPosition, SortPageParams, build_sorted_page, paginate_query_sorted
from app.core.sorting import SortField
from app.platform.models.dealership import DealerGroup

_BY_NAME = [SortField(api_name="name", column=DealerGroup.name, direction="asc", nullable=False)]


def _contains_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_dealer_group_options(
    db: Session, *, q: str | None, limit: int, cursor: SortCursorPosition | None
) -> tuple[list[DealerGroup], str | None]:
    """The groups whose name contains `q` (case-insensitive; `%` and `_` are
    literal), by name; a blank `q` lists every group, still a page at a time."""

    if cursor is not None and (len(cursor.values) != 1 or not isinstance(cursor.values[0], str)):
        raise BadRequestError("Cursor is malformed or invalid.")
    params = SortPageParams(limit=limit, cursor=cursor, sort_fields=_BY_NAME)
    stmt = select(DealerGroup)
    term = (q or "").strip()
    if term:
        stmt = stmt.where(DealerGroup.name.ilike(_contains_pattern(term), escape="\\"))
    rows = list(db.scalars(paginate_query_sorted(stmt, model=DealerGroup, params=params)))
    return build_sorted_page(rows, params)

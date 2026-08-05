"""Optimistic concurrency: If-Match / version pattern shared by every
mutating entity endpoint (PATCH, status transitions, etc.).

Usage in an entity router:
    def update_widget(widget_id, body, if_match: int = Depends(require_if_match), db=Depends(get_db)):
        widget = get_or_404(db, Widget, widget_id, tenant_id)
        check_version(widget, if_match)
        ... mutate + widget.version += 1 ...
"""

from fastapi import Header

from app.core.errors import BadRequestError, ConflictError


def require_if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    if if_match is None:
        raise BadRequestError("If-Match header is required for this update.")
    try:
        return int(if_match.strip('"'))
    except ValueError as exc:
        raise BadRequestError("If-Match header must be an integer version.") from exc


def check_version(current_version: int, if_match_version: int, *, entity_name: str = "resource") -> None:
    if current_version != if_match_version:
        raise ConflictError(
            f"{entity_name} has been modified since If-Match version {if_match_version} "
            f"(current version is {current_version}).",
            details={"currentVersion": current_version, "ifMatchVersion": if_match_version},
        )

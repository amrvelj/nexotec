"""PATCH bodies whose fields are all `X | None = None`, where None means
"not sent": an explicit `null` on a field whose column is NOT NULL would
reach the database (500) or a misleading 409. It is refused here instead,
as the error taxonomy's 422 (KAN-136, KAN-312). Null on a nullable field
keeps clearing it.
"""

from collections.abc import Collection

from pydantic import BaseModel


def refuse_null_on_not_null_fields(body: BaseModel, *, nullable: Collection[str] = ()) -> None:
    """Raises ValueError (422 at the API) naming each field sent as null
    that is not in `nullable` (Python field names)."""

    cleared = [
        field.alias or name
        for name, field in type(body).model_fields.items()
        if name not in nullable and name in body.model_fields_set and getattr(body, name) is None
    ]
    if cleared:
        raise ValueError(f"{', '.join(cleared)} cannot be null — leave the field out to keep its value.")

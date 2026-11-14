"""UserPreference schemas.

The payload shape is deliberately opaque to the server — a grid's column
layout and the sidebar's collapsed flag have nothing in common, and the
whole point of this platform capability is that a new module never needs a
backend schema to store its own layout state. The server validates only the
envelope: `scope`, overall size, and that `schemaVersion` is present.
"""

import datetime as dt

from pydantic import ConfigDict, Field

from app.core.schemas import CamelModel

# Grid/scope keys are namespaced strings like "grid:mdm.customers.list" or
# "views:mdm.customers.list" (UI/UX Core Principles § User-Level Preference
# Persistence) — restrict to the safe charset actually used by that scheme
# rather than accepting arbitrary text. Used as a FastAPI Path(pattern=...)
# constraint in app.api.v1.user_preferences.
SCOPE_PATTERN = r"^[a-zA-Z0-9_.:\-]{1,128}$"


class UserPreferenceWrite(CamelModel):
    """PUT body. `extra="allow"` because the module-specific preference
    fields (sort, density, columns, ...) are not known to the platform
    layer — see module docstring. model_config here merges with (does not
    replace) CamelModel's, per Pydantic v2 subclass config inheritance.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = Field(default=1, ge=1)


class UserPreferenceRead(CamelModel):
    scope: str
    payload: dict
    # None when the caller has never written this scope (or has deleted
    # it) — distinct from an actual write at some real timestamp, so the
    # frontend can tell "no override, use the module default" apart from
    # "there's a stored value".
    updated_at: dt.datetime | None


class UserPreferenceListRead(CamelModel):
    items: list[UserPreferenceRead]

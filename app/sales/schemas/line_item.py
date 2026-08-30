"""SalesLineItem schemas (WP-8 PR-8, S-D14). One PUT body replaces the
whole line-item picture in a single call — accessories fully (add/update/
remove by presence of `id`) and factory options by per-id patch only
(never created/deleted here; they're system-managed by
services/snapshot.py::freeze_vehicle_snapshot).
"""

import uuid
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.sales.models.line_item import LineItemKind


class LineItemAccessoryInput(CamelModel):
    # None = a new row; set = update the existing row with this id (any
    # existing accessory row whose id is NOT present in the submitted list
    # is deleted — a full replace, per the module's own "PUT body" idiom).
    id: uuid.UUID | None = None
    code: str
    label: str
    unit_price: Decimal
    quantity: int = 1
    discount_type: str | None = None  # "percent" | "amount"
    discount_value: Decimal | None = None
    # Required to set a discount on this line when the vehicle is used
    # (see module docstring on services/line_items.py) — optional
    # otherwise.
    discount_suppressed_reason: str | None = None


class LineItemFactoryOptionPatch(CamelModel):
    """A factory-option row always exists already (frozen at snapshot
    time) — this patches `included` and/or its own per-line discount,
    never the row's identity (code/label/unitPrice come from the frozen
    snapshot and are not editable here)."""

    id: uuid.UUID
    included: bool
    discount_type: str | None = None
    discount_value: Decimal | None = None
    discount_suppressed_reason: str | None = None


class LineItemsReplaceRequest(CamelModel):
    accessories: list[LineItemAccessoryInput] = Field(default_factory=list)
    factory_options: list[LineItemFactoryOptionPatch] = Field(default_factory=list)


class LineItemRead(CamelModel):
    id: uuid.UUID
    kind: LineItemKind
    code: str
    label: str
    unit_price: Decimal
    quantity: int
    included: bool
    discount_type: str | None
    discount_value: Decimal | None
    discount_resolved_amount: Decimal | None
    discount_suppressed_reason: str | None
    position: int


class LineItemPage(CamelModel):
    items: list[LineItemRead]

import datetime as dt
from decimal import Decimal

from pydantic import Field

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVin


class RecordPurchaseRequest(CamelModel):
    supplier_name: str
    supplier_is_vat_registered: bool
    purchase_price: Decimal
    purchase_date: dt.date
    purchase_invoice_ref: str | None = None
    landed_cost: Decimal | None = None
    vin: NormalisedVin | None = Field(
        default=None,
        description=(
            "KAN-446 (ADR-045, FR-V-04): the car's VIN. Required to book the purchase of a pipeline item, "
            "which the booking promotes into stock with it (422 `purchase_requires_vin` without one; 409 "
            "`vin_already_in_stock` when another item of this dealership holds it in stock). On an item "
            "already in stock it may be sent and must be the item's own (422 `purchase_vin_mismatch`)."
        ),
    )


class NotionalInputTaxOverrideRequest(CamelModel):
    """FR-I-xx / Art. 28a MWSTG. `reason` is required — an override is
    always an audited entry naming the actor, the prefilled value, the
    chosen value and why (app/core/audit.py::record_audit_event), never a
    silent overwrite.
    """

    applicable: bool
    rate: Decimal | None = None
    reason: str

import datetime as dt
from decimal import Decimal

from app.core.schemas import CamelModel


class RecordPurchaseRequest(CamelModel):
    supplier_name: str
    supplier_is_vat_registered: bool
    purchase_price: Decimal
    purchase_date: dt.date
    purchase_invoice_ref: str | None = None
    landed_cost: Decimal | None = None


class NotionalInputTaxOverrideRequest(CamelModel):
    """FR-I-xx / Art. 28a MWSTG. `reason` is required — an override is
    always an audited entry naming the actor, the prefilled value, the
    chosen value and why (app/core/audit.py::record_audit_event), never a
    silent overwrite.
    """

    applicable: bool
    rate: Decimal | None = None
    reason: str

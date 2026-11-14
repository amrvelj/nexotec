import datetime as dt
from decimal import Decimal

from pydantic import Field, field_validator

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVinOrChassisNumber


class RecordPurchaseRequest(CamelModel):
    supplier_name: str
    supplier_is_vat_registered: bool
    purchase_price: Decimal
    purchase_date: dt.date
    purchase_invoice_ref: str | None = None
    landed_cost: Decimal | None = None
    vin: NormalisedVinOrChassisNumber | None = Field(
        default=None,
        description=(
            "KAN-446 (ADR-045, FR-V-04): the car's VIN. Required to book the purchase of a pipeline item, "
            "which the booking promotes into stock with it (422 `purchase_requires_vin` without one; 409 "
            "`vin_already_in_stock` when another item of this dealership holds it in stock) — unless the item "
            "already carries its VIN (created with one, KAN-554; or stornoed, KAN-146): then it may be left out "
            "and, when sent, must be the item's own (422 `purchase_vin_mismatch`). One created with its VIN and "
            "never bought is the exception: when its configuration's VIN has changed since, the configuration's "
            "wins and the booking corrects the item's (audited `vin_corrected_at_purchase`, KAN-575). When the item's "
            "configuration carries a VIN, it must be that one (422 `configuration_vin_differs`, KAN-510; the "
            "VIN is corrected on the configuration). A trade-in whose configuration carries none is bought under "
            "the VIN its contract holds (the item's `tradeInVin`; 422 `trade_in_vin_differs`, KAN-536). On an item already in stock it may be sent and must be "
            "the item's own (422 `purchase_vin_mismatch`). KAN-535 (ADR-103 as amended): a car first registered before "
            "1981 may be bought under its chassis number (1 to 17 letters and digits) instead: its configuration "
            "must carry that number (422 `chassis_number_requires_configuration`), a first registration before "
            "1981 (422 `chassis_number_requires_pre_1981_registration`) and the make (422 "
            "`chassis_number_requires_make`); its vehicle-mdm record is the number together with the make."
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


class PurchaseStornoRequest(CamelModel):
    """FR-I-05 (KAN-146): «Einkauf stornieren» is audited with its reason —
    required, never empty."""

    reason: str = Field(min_length=1, max_length=500)
    returned_to_customer: bool = Field(
        default=False,
        description=(
            "KAN-572 (Anto, 2026-11-07, option C): the trade-in went back to the customer who traded it in, so "
            "the customer context reopens that customer as the car's owner and keeper. Only a trade-in's car "
            "(422 `storno_return_not_trade_in` otherwise); false — the default — leaves the customer's history "
            "as the purchase wrote it: the customer did hand the car over."
        ),
    )

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped

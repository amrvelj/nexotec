"""StockItem schemas (WP-7 PR-1).

`vin` is writable only when an item is created (`StockItemCreate`, a car
with a known VIN taken straight into stock) and by the promotion path
(PR-2, ADR-045) — never by an update: `StockItemUpdate` omits it, matching
app.vehicle.schemas.vehicle_mdm's own rule that a vehicle's VIN is
corrected only through VehicleMdmUpdate.

On create the VIN is normalised and then validated exactly as vehicle-mdm
validates it (KAN-244, Anto's option A), and the item's vehicle is resolved
from it, never taken from the client (option C) — see `StockItemCreate`.
Without a VIN there is no vehicle to name: a `vehicleId` sent without one is
refused (KAN-324, Anto's option A), never stored unverified.
"""

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, model_validator
from pydantic_core import PydanticCustomError

from app.core.schemas import CamelModel
from app.core.validators import Vin
from app.inventory.models.stock_item import AgeingBucket, LifecycleStatus, ReservationState, StockItemCondition


def _normalise_vin(value: Any) -> Any:
    """KAN-244 (Anto, option A): upper-cased, every whitespace character
    removed — a VIN typed in lower case or pasted in groups is the same VIN.
    Anything else wrong with it is left for `Vin` to refuse (422)."""

    if isinstance(value, str):
        return re.sub(r"\s", "", value).upper()
    return value


# Normalised first, then vehicle-mdm's own rule (17 characters, uppercase
# letters and digits, never I, O or Q), so the KAN-111 duplicate check and
# its index compare one spelling.
NormalisedVin = Annotated[Vin, BeforeValidator(_normalise_vin)]


class StockItemCreate(CamelModel):
    vehicle_label: str
    condition: StockItemCondition
    location_id: uuid.UUID | None = None
    odometer_km: int | None = None
    list_price: Decimal | None = None
    effective_price: Decimal | None = None
    first_registration_date: dt.date | None = None
    # KAN-244 (Anto, option C): with a VIN, the vehicle is the vehicle-mdm
    # record that VIN resolves to (found, or created — as promotion does);
    # a vehicleId the client sends is ignored, not refused, so a client that
    # sends one today keeps working. Without a VIN (a pipeline-style create)
    # a vehicleId is refused — 422 `vehicle_id_requires_vin`, the validator
    # below (KAN-324, Anto's option A): a pipeline item has no vehicle until
    # its VIN arrives, and one stored unverified named a record nobody had
    # checked exists, or another car's, until a promotion replaced it.
    vehicle_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Ignored when `vin` is sent: the item's vehicle is then the vehicle-mdm record that VIN resolves "
            "to (found, or created). Without a `vin` it is refused (422 `vehicle_id_requires_vin`): a pipeline "
            "item has no vehicle until its VIN arrives."
        ),
    )
    vin: NormalisedVin | None = Field(
        default=None,
        description=(
            "A car with a known VIN, taken straight into stock. Normalised first (upper-cased, all whitespace "
            "removed), then validated as vehicle-mdm validates a VIN: 17 characters, uppercase letters and "
            "digits, never I, O or Q — otherwise 422. A VIN already in this dealership's stock is 409 "
            "`vin_already_in_stock`."
        ),
    )
    # C-F (KAN-10, FR-C-13) — "add to pipeline" from the Stock list: the
    # configuration the configurator just produced, `build` for a factory
    # order or `record` for a car being bought in (both modes allowed).
    configuration_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _vehicle_id_requires_vin(self) -> "StockItemCreate":
        # On the schema, not in the service, so every writer that builds a
        # StockItemCreate (the route, the contract-confirmation consumer, a
        # script) meets the rule. A named type, as a VIN's own 422 carries
        # the validator's text, so a client can tell the two apart.
        if self.vehicle_id is not None and self.vin is None:
            raise PydanticCustomError(
                "vehicle_id_requires_vin",
                "vehicleId is only accepted with a vin: a pipeline item has no vehicle until its VIN arrives, "
                "and with a vin the vehicle is resolved from it.",
            )
        return self


class StockItemUpdate(CamelModel):
    vehicle_label: str | None = None
    condition: StockItemCondition | None = None
    location_id: uuid.UUID | None = None
    odometer_km: int | None = None
    exterior_colour: str | None = None
    body_style: str | None = None
    list_price: Decimal | None = None
    effective_price: Decimal | None = None
    first_registration_date: dt.date | None = None


class StockItemRead(CamelModel):
    id: uuid.UUID
    stock_number: str
    vehicle_id: uuid.UUID | None
    vin: str | None
    vehicle_label: str
    lifecycle_status: LifecycleStatus
    reservation_state: ReservationState
    condition: StockItemCondition
    location_id: uuid.UUID | None
    odometer_km: int | None
    exterior_colour: str | None
    body_style: str | None
    list_price: Decimal | None
    effective_price: Decimal | None
    first_registration_date: dt.date | None
    pipeline_ref: str | None
    configuration_id: uuid.UUID | None = None
    configuration_label: str | None = None
    order_date: dt.date | None
    expected_delivery: dt.date | None
    in_stock_at: dt.datetime | None
    supplier_name: str | None
    supplier_is_vat_registered: bool | None
    purchase_date: dt.date | None
    purchase_price: Decimal | None
    purchase_invoice_ref: str | None
    landed_cost: Decimal | None
    notional_input_tax_applicable: bool | None
    notional_input_tax_rate: Decimal | None
    notional_input_tax_amount: Decimal | None
    notional_input_tax_overridden: bool
    is_invoiceable: bool
    left_stock_at: dt.datetime | None
    # WP-7 PR-9 (FR-I-22) — the base price a factory-option list adds to.
    base_price: Decimal | None
    # WP-7 PR-9 (ADR-066/ADR-048) — a denormalized pointer only; the
    # valuation module (WP-8) is the single writer of the real record.
    valuation_ref_id: uuid.UUID | None
    valuation_ref_amount: Decimal | None
    valuation_ref_valued_at: dt.datetime | None
    valuation_ref_source: str | None
    # Computed on read (PR-7), never stored — see
    # services/stock_item.py::compute_ageing_bucket. None until the model
    # is post-processed by the API layer; model_validate alone leaves it
    # at this default, same pattern as customer's own computed
    # projections (customer/api/customers.py::_customer_read).
    ageing_bucket: AgeingBucket | None = None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class StockItemConditionChange(CamelModel):
    condition: StockItemCondition


class StockItemPage(CamelModel):
    items: list[StockItemRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

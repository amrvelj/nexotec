"""StockItem schemas (WP-7 PR-1).

`vin` is writable only when an item is created (`StockItemCreate`, a car
whose VIN is known before it is bought — it lands in pipeline carrying it,
KAN-554) and by the promotion path
(PR-2, ADR-045) — never by an update: `StockItemUpdate` omits it, matching
app.vehicle.schemas.vehicle_mdm's own rule that a vehicle's VIN is
corrected only through VehicleMdmUpdate.

On create the VIN is normalised and then validated exactly as vehicle-mdm
validates it (KAN-244, Anto's option A). The item gets no vehicle on create:
its vehicle-mdm record is resolved from the VIN at purchase booking, never
taken from the client (KAN-244 option C; KAN-554 option A, ADR-116).
Without a VIN there is no vehicle to name: a `vehicleId` sent without one is
refused (KAN-324, Anto's option A), never stored unverified.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from app.core.schemas import CamelModel
from app.core.validators import NormalisedVin
from app.inventory.models.stock_item import AgeingBucket, LifecycleStatus, ReservationState, StockItemCondition


class StockItemCreate(CamelModel):
    vehicle_label: str
    condition: StockItemCondition
    location_id: uuid.UUID | None = None
    odometer_km: int | None = None
    list_price: Decimal | None = None
    effective_price: Decimal | None = None
    first_registration_date: dt.date | None = None
    # KAN-244 (Anto, option C): with a VIN, a vehicleId the client sends is
    # ignored, not refused, so a client that sends one today keeps working;
    # since KAN-554 (option A) the item's vehicle is resolved from the VIN
    # at purchase booking, not on create. Without a VIN (a pipeline-style create)
    # a vehicleId is refused — 422 `vehicle_id_requires_vin`, the validator
    # below (KAN-324, Anto's option A): a pipeline item has no vehicle until
    # its VIN arrives, and one stored unverified named a record nobody had
    # checked exists, or another car's, until a promotion replaced it.
    vehicle_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Ignored when `vin` is sent: the item's vehicle is the vehicle-mdm record its VIN resolves to "
            "(found, or created) when its purchase is booked. Without a `vin` it is refused (422 "
            "`vehicle_id_requires_vin`): a pipeline item has no vehicle until it is bought."
        ),
    )
    vin: NormalisedVin | None = Field(
        default=None,
        description=(
            "The car's VIN, when it is known before the car is bought. The item still lands in pipeline "
            "(`lifecycleStatus: pipeline`, ADR-103/ADR-116): only booking its purchase puts it in stock, under "
            "this VIN. Normalised first (upper-cased, all whitespace removed), then validated as vehicle-mdm "
            "validates a VIN: 17 characters, uppercase letters and digits, never I, O or Q — otherwise 422. A "
            "VIN another item of this dealership holds (in stock, or in pipeline with it) is 409 "
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


class StockItemCreateRequest(StockItemCreate):
    """KAN-463 (Anto, 2026-10-26/28, option A): the body of
    `POST /v1/inventory/stock-items`. Every stock vehicle a user adds enters
    through the configurator, so the public endpoint refuses a car without
    the configuration it produced — the hand-typed intake, which created a
    label with no vehicle and no technical data, is gone. Enforced here, at
    the endpoint only (Anto, decision 1a): the contract-confirmation
    consumers (FR-I-02/02b) build a `StockItemCreate` of their own and the
    ADR-091 legacy import writes its rows directly — neither passes this
    door."""

    configuration_id: uuid.UUID = Field(
        description=(
            "The configuration the configurator produced for this car (`build` for a factory order, `record` "
            "for a car being bought in). Required: without it the create is 422 `configuration_required`."
        ),
    )
    # ADR-117 (2), KAN-556: a configured car's name is its configuration's
    # label, derived server-side and refreshed with it — no longer the
    # client's free text. Optional, and ignored when sent, so a client that
    # still sends it keeps working.
    vehicle_label: str | None = Field(  # type: ignore[assignment]
        default=None,
        description=(
            "Ignored: the stock item's name (`vehicleLabel`) is its configuration's label, derived by the "
            "server and refreshed whenever the configuration changes (ADR-117)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _configuration_required(cls, data: Any) -> Any:
        # A named type, as `vehicle_id_requires_vin`'s, so a client can tell
        # this refusal from a malformed id.
        if isinstance(data, dict) and data.get("configurationId", data.get("configuration_id")) is None:
            raise PydanticCustomError(
                "configuration_required",
                "configurationId is required: a stock vehicle is added through the configurator.",
            )
        return data


class StockItemConfigurationAttach(CamelModel):
    """KAN-463 — «Konfiguration zuordnen»: links the configuration the
    configurator produced to a stock item that has none (one created by the
    retired hand-typed intake). Refused when one is already linked."""

    configuration_id: uuid.UUID


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

    @field_validator("vehicle_label", "condition")
    @classmethod
    def _not_null_column_not_cleared(cls, value: Any, info: ValidationInfo) -> Any:
        # KAN-441 (KAN-394's pattern): both columns are NOT NULL on the
        # model. A field validator runs only for a key the body sends, so
        # `None` here is an explicit null — a malformed request (422 naming
        # the field), not a NotNullViolation at the flush (a 500). Leaving
        # the key out keeps the value.
        if value is None:
            raise ValueError(f"{to_camel(info.field_name or '')} cannot be cleared — send a value or leave the field out.")
        return value


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


class StockItemVinCorrectionRequest(CamelModel):
    """KAN-557 «VIN korrigieren» (ADR-116 (2)-(3)): the corrected VIN —
    normalised and validated as vehicle-mdm does (KAN-244, 422 otherwise) —
    and the reason, required, audited on every record the correction
    rewrites."""

    new_vin: NormalisedVin = Field(
        description=(
            "The car's corrected VIN. It replaces the VIN on the stock item, on its vehicle-mdm record and on its "
            "configuration together (KAN-557)."
        )
    )
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class StockItemPage(CamelModel):
    items: list[StockItemRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

"""SalesContract schemas (WP-8 PR-1)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from app.core.schemas import CamelModel
from app.sales.models.contract import ContractStatus, FinancingKind
from app.sales.schemas.offer import OfferContainerState


class ContractCreate(CamelModel):
    # None = a direct "Vertrag erstellen" contract with no prior offer
    # (confirmed live as the stock detail header's own primary action).
    offer_id: uuid.UUID | None = None
    # KAN-58 — the customer→contract entry point (FR-22 "New contract" on
    # the customer row menu / 360 overflow / offers-and-contracts tab). An
    # offer already carries its own customer, so this and offer_id are
    # mutually exclusive — see the validator below.
    customer_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _offer_xor_customer(self) -> "ContractCreate":
        if self.offer_id is not None and self.customer_id is not None:
            raise ValueError(
                "offerId and customerId cannot both be set — a contract born from an offer already has that "
                "offer's customer."
            )
        return self


class ContractUpdate(CamelModel):
    """KAN-67 (ADR-083) — completes or adjusts a PENDING contract: its
    vehicle (FR-S-05 Path A: a stock item; Path B: a `build` configuration;
    or a manually described car) and the seller's own pricing inputs. Every
    field is optional and applied incrementally, like the offer's autosave
    PATCH (OfferUpdate) and with the same meaning; every derived figure is
    recomputed server-side, never taken from the client. The customer is
    not editable here — a contract keeps the customer it was created for.
    The trade-in and the line items have their own routes, as on the offer.
    """

    vehicle_source: Literal["stock", "manual"] | None = None
    stock_item_id: uuid.UUID | None = None
    vehicle_label: str | None = Field(default=None, max_length=200)
    manual_vehicle_condition: str | None = Field(default=None, max_length=16)
    manual_base_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    # FR-C-12, `build` mode only. Setting it makes the vehicle a
    # configuration; null detaches it.
    configuration_id: uuid.UUID | None = None
    discount_type: Literal["percent", "amount"] | None = None
    discount_value: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class ContractRead(CamelModel):
    id: uuid.UUID
    contract_number: str
    offer_id: uuid.UUID | None
    offer_number: str | None
    status: ContractStatus
    customer_id: uuid.UUID | None
    customer_label: str | None
    customer_locality: str | None
    vehicle_source: str | None
    stock_item_id: uuid.UUID | None
    vehicle_label: str | None
    configuration_id: uuid.UUID | None = None
    configuration_label: str | None = None
    manual_vehicle_condition: str | None
    # KAN-67 — the pricing inputs and the full build-up, as on OfferRead.
    manual_base_price: Decimal | None = None
    vehicle_snapshot_frozen_at: dt.datetime | None = None
    base_price: Decimal | None = None
    options_total: Decimal | None = None
    list_price: Decimal | None = None
    accessories_total: Decimal | None = None
    total_before_discount: Decimal | None = None
    discount_type: str | None = None
    discount_value: Decimal | None = None
    discount_amount: Decimal | None = None
    gross_price: Decimal | None
    # ADR-049/029 — entity-private, like OfferRead's: no group-scoped
    # endpoint returns a contract.
    cost_basis: Decimal | None = None
    margin: Decimal | None
    trade_in_vehicle_id: uuid.UUID | None
    trade_in_label: str | None
    trade_in_vin: str | None
    trade_in_configuration_id: uuid.UUID | None = None
    trade_in_valuation_id: uuid.UUID | None
    trade_in_value: Decimal | None
    trade_in_purchase_price: Decimal | None
    payable: Decimal | None
    financing: FinancingKind | None
    reservation_id: uuid.UUID | None
    signed_at: dt.datetime | None
    delivery_date: dt.date | None
    is_invoiceable: bool
    # KAN-147 — migrated from the legacy `transaction` table (ADR-050): the
    # old system invoiced it, so it can be neither invoiced nor cancelled.
    is_legacy: bool
    invoice_ref: str | None
    cancelled_reason: str | None
    # KAN-67 — computed in the API's _contract_read, never stored (as on
    # OfferRead): what is still missing, and the condition the per-line
    # discount rule reads.
    containers: list[OfferContainerState] = Field(default_factory=list)
    vehicle_condition: str | None = None
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ContractCancelRequest(CamelModel):
    reason: str


class ContractPage(CamelModel):
    items: list[ContractRead]
    next_cursor: str | None
    total: int
    total_is_estimate: bool

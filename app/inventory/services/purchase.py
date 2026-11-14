"""Purchase, landed cost, fiktiver Vorsteuerabzug (WP-7 PR-3, Art. 28a
MWSTG). ADR-057: there is NO vatTreatment field anywhere in this module —
notional_input_tax_* is a purchase-side fact belonging to Stock's own
acquisition booking, never shown on a customer document, never read or
written by Sales.
"""

import datetime as dt
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableEntityError
from app.core.outbox import OutboxEvent, publish
from app.core.validators import is_chassis_number
from app.inventory.models.stock_item import LifecycleStatus, StockItem
from app.inventory.schemas.purchase import (
    NotionalInputTaxOverrideRequest,
    PurchaseStornoRequest,
    RecordPurchaseRequest,
)
from app.inventory.services.ledger import post_purchase_entries, reverse_purchase_entries
from app.inventory.services.pipeline import promote_and_flush
from app.inventory.services.sales_contract import contract_with_invoice_requested
from app.inventory.services.stock_item import (
    configuration_vin_differs,
    configuration_vin_differs_error,
    mark_purchased_if_ready,
    pipeline_origin,
    trade_in_seller_fields,
    trade_in_vin_differs,
    trade_in_vin_differs_error,
)
from app.platform.public import get_dealership_or_404
from app.vehicle.public import get_configuration_for_host

_EVENT_PRODUCER = "inventory"


def _compute_notional_input_tax(*, purchase_price: Decimal, rate: Decimal | None) -> Decimal | None:
    """Art. 28a MWSTG: amount = rate / (100 + rate) * purchase price. None
    when the dealership hasn't configured a vat_rate yet — applicable
    stays recorded, the amount is simply not computable until it does.
    """

    if rate is None:
        return None
    return (purchase_price * rate / (Decimal(100) + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def record_purchase(
    db: Session, *, item: StockItem, data: RecordPurchaseRequest, actor_id: uuid.UUID | None
) -> StockItem:
    """Prefills notional_input_tax_applicable from the supplier's own VAT-
    registration status — false for a VAT-registered business (no fiktiver
    Vorsteuerabzug: real input tax was already deducted along the normal
    chain), true for a private individual (the dealer paid no VAT on
    acquisition, so Art. 28a lets it deduct a notional amount instead).

    KAN-446 (Anto, 2026-10-25): purchase booking is what puts a car on stock.
    A pipeline item cannot be bought without its VIN; with one, it is
    promoted (FR-V-04) in this same transaction before the purchase facts
    are written, so `mark_purchased_if_ready` below publishes
    `inventory.stock_item.purchased` at once. A refused promotion (a VIN
    already in stock) writes neither, nor does a VIN that is not the item's
    configuration's (422 `configuration_vin_differs`, KAN-510) or, for a
    trade-in whose configuration carries none, not the one its contract
    holds (422 `trade_in_vin_differs`, KAN-536).
    """

    # KAN-146 (Anto, 2026-11-05): a purchase is booked once; a second booking
    # waits for its storno, which reverses the first one's Wagenbuch entry
    # and returns the car to pipeline. Overwriting the facts would leave the
    # first Einstandspreis in the book and the audit trail.
    if item.purchase_price is not None:
        raise ConflictError(
            f"The purchase of stock item {item.stock_number} is already booked — storno it before booking again.",
            details={"reason": "purchase_already_booked", "stockItemId": str(item.id)},
        )

    if item.lifecycle_status == LifecycleStatus.PIPELINE and item.vehicle_id is not None:
        # A stornoed car (KAN-146): it kept its VIN and its vehicle-mdm record
        # on the pipeline item (Anto, 2026-11-05) and still blocks that VIN in
        # stock (FR-I-12), so booking it again promotes nothing — it returns to
        # stock under the VIN it has. Another VIN describes another car.
        if data.vin is not None and data.vin != item.vin:
            raise UnprocessableEntityError(
                f"Stock item {item.id} carries VIN {item.vin}, not {data.vin}.",
                details={"reason": "purchase_vin_mismatch", "stockItemId": str(item.id)},
            )
        item.lifecycle_status = LifecycleStatus.IN_STOCK
        item.in_stock_at = utcnow()
    elif item.lifecycle_status == LifecycleStatus.PIPELINE:
        if item.vin is None and data.vin is None:
            raise UnprocessableEntityError(
                f"Stock item {item.id} is a pipeline item: its purchase needs the car's VIN.",
                details={"reason": "purchase_requires_vin", "stockItemId": str(item.id)},
            )
        # KAN-510 (Anto, 2026-11-01): the configuration is the car's
        # technical data (KAN-463), so a VIN that is not its own would put
        # another car on stock under it — refused as the create and
        # «Konfiguration zuordnen» refuse it, before vehicle-mdm or the item
        # is touched. A configuration without a VIN (a factory order) takes
        # the typed one.
        configuration = (
            get_configuration_for_host(db, tenant_id=item.tenant_id, configuration_id=item.configuration_id)
            if item.configuration_id is not None
            else None
        )
        configuration_vin = configuration.vin if configuration is not None else None
        # KAN-554 (ADR-116): a pipeline item created with its VIN is bought
        # under that VIN — the request may repeat it or leave it out, never
        # name another car. KAN-575 (Anto, 2026-11-07, option A): except that
        # while it was never bought, its configuration's VIN wins — the VIN
        # corrected there («Konfiguration bearbeiten», KAN-510) or given to a
        # factory order later — and the booking corrects the item's, audited
        # (`vin_corrected_at_purchase`). Before, the two refusals below met
        # and the car could not be bought at all.
        corrects_item_vin = (
            item.vin is not None
            and configuration_vin is not None
            and configuration_vin_differs(configuration_vin, item.vin)
        )
        if item.vin is not None and data.vin is not None and data.vin != item.vin and not corrects_item_vin:
            raise UnprocessableEntityError(
                f"Stock item {item.id} carries VIN {item.vin}, not {data.vin}.",
                details={"reason": "purchase_vin_mismatch", "stockItemId": str(item.id)},
            )
        vin: str | None
        if data.vin is not None:
            vin = data.vin
        elif corrects_item_vin:
            vin = configuration_vin
        else:
            vin = item.vin
        assert vin is not None  # one of the request's, the configuration's or the item's (refused above)
        if configuration_vin_differs(configuration_vin, vin):
            raise configuration_vin_differs_error(field="vin")
        # KAN-536 (Anto, 2026-11-12, option A): a trade-in is bought under
        # the VIN its contract holds — another is refused the same way,
        # before vehicle-mdm or the item is touched. A configuration's VIN,
        # checked above, decides alone when there is one.
        if trade_in_vin_differs(item, configuration_vin=configuration_vin, vin=vin):
            raise trade_in_vin_differs_error()
        # KAN-535 (ADR-103 as amended; Anto, 2026-11-13, option A): a car
        # first registered before 1981 is bought under its chassis number.
        # Its vehicle-mdm record is that number together with its make, both
        # read off its configuration — the one place the number was checked
        # against the first registration (KAN-353) and the make typed. Without
        # a configuration carrying the number there is no make to tell the car
        # by: refused before vehicle-mdm or the item is touched.
        chassis_make = first_registration = None
        if is_chassis_number(vin):
            if configuration is None or configuration.vin is None:
                raise UnprocessableEntityError(
                    "A car with a chassis number instead of a VIN is bought under its configuration: attach "
                    "its configuration («Konfiguration zuordnen») with the chassis number, the make and the "
                    "first registration.",
                    details={"field": "vin", "reason": "chassis_number_requires_configuration", "stockItemId": str(item.id)},
                )
            chassis_make = configuration.brand_display_name or ""
            first_registration = configuration.first_registration_date
        vin_before = item.vin
        item = promote_and_flush(
            db, item=item, vin=vin, actor_id=actor_id, correct_vin=corrects_item_vin,
            chassis_make=chassis_make, first_registration_date=first_registration,
        )
        if corrects_item_vin:
            record_audit_event(
                db,
                entity_type="stock_item",
                entity_id=item.id,
                tenant_id=item.tenant_id,
                action="vin_corrected_at_purchase",
                actor_id=actor_id,
                before={"vin": vin_before},
                after={"vin": item.vin},
                reason="configuration_vin_wins",
            )
    elif data.vin is not None and data.vin != item.vin:
        raise UnprocessableEntityError(
            f"Stock item {item.id} carries VIN {item.vin}, not {data.vin}.",
            details={"reason": "purchase_vin_mismatch", "stockItemId": str(item.id)},
        )

    dealership = get_dealership_or_404(db, item.tenant_id)
    applicable = not data.supplier_is_vat_registered

    item.supplier_name = data.supplier_name
    item.supplier_is_vat_registered = data.supplier_is_vat_registered
    item.purchase_price = data.purchase_price
    item.purchase_date = data.purchase_date
    item.purchase_invoice_ref = data.purchase_invoice_ref
    item.landed_cost = data.landed_cost
    item.notional_input_tax_applicable = applicable
    item.notional_input_tax_rate = dealership.vat_rate if applicable else None
    item.notional_input_tax_amount = (
        _compute_notional_input_tax(purchase_price=data.purchase_price, rate=dealership.vat_rate)
        if applicable
        else None
    )
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    # KAN-146: the Einstandspreis is an automatic Wagenbuch position
    # (FR-I-15a), posted with the booking, dated the purchase date's start
    # in Switzerland; the booking is audited like every other purchase-side
    # write.
    post_purchase_entries(db, item=item, occurred_at=_start_of_swiss_day(data.purchase_date), actor_id=actor_id)
    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="purchase_booked",
        actor_id=actor_id,
        after=_purchase_facts(item),
    )
    mark_purchased_if_ready(db, item)
    db.commit()
    db.refresh(item)
    return item


def _start_of_swiss_day(day: dt.date) -> dt.datetime:
    """The start of a calendar day in Switzerland, in UTC — the moment a
    date-only business fact (the purchase date) is booked at."""

    return dt.datetime.combine(day, dt.time(), tzinfo=ZoneInfo(get_settings().session_timezone)).astimezone(dt.UTC)


def _purchase_facts(item: StockItem) -> dict[str, Any]:
    """The booked purchase as the audit trail keeps it — what a storno's
    `before` shows was reversed."""

    def text(value: object) -> str | None:
        return None if value is None else str(value)

    return {
        "supplierName": item.supplier_name,
        "supplierIsVatRegistered": item.supplier_is_vat_registered,
        "purchasePrice": text(item.purchase_price),
        "purchaseDate": item.purchase_date.isoformat() if item.purchase_date else None,
        "purchaseInvoiceRef": item.purchase_invoice_ref,
        "landedCost": text(item.landed_cost),
        "notionalInputTaxApplicable": item.notional_input_tax_applicable,
        "notionalInputTaxRate": text(item.notional_input_tax_rate),
        "notionalInputTaxAmount": text(item.notional_input_tax_amount),
        "notionalInputTaxOverridden": item.notional_input_tax_overridden,
        "lifecycleStatus": item.lifecycle_status.value,
        "isInvoiceable": item.is_invoiceable,
    }


def storno_purchase(
    db: Session, *, item: StockItem, data: PurchaseStornoRequest, actor_id: uuid.UUID | None
) -> StockItem:
    """FR-I-05 «Einkauf stornieren» (KAN-146; Anto, 2026-11-05). Reverses a
    booked purchase in one transaction: the Wagenbuch counter-entries
    (append, never an edit), the purchase facts cleared, the car back in
    pipeline — keeping its VIN and vehicle-mdm record, so it still blocks
    that VIN in stock and books again without a new one — the audit row with
    the reason, and `inventory.stock_item.storno`, on which Sales deletes its
    purchase replica so every contract on the car derives «nicht
    fakturierbar» until it is bought again (ADR-052).

    A reserved car may be stornoed: its reservation stays. Refused when no
    purchase is booked (409 `purchase_not_booked`), when the car has left
    stock (409 `purchase_storno_left_stock`, FR-I-12) and when a contract on it has
    requested its invoice (409 `purchase_storno_invoice_requested`, with
    `contractNumber`):
    the hand-off to Finance happened, so for the business the car is
    invoiced. Since KAN-630 (ADR-131) Stock answers that from its own
    projection of Sales' contract facts, counting a request Sales has
    committed that the projection has not applied yet — so the outbox's
    delay never lets a storno through that the direct read would have
    refused (app.inventory.services.sales_contract).

    KAN-572 (Anto, 2026-11-07, option C): `returnedToCustomer` says a
    trade-in went back to the customer who traded it in. The audit row and
    the event carry it (with the cancelled purchase date and the trade-in's
    contract), and the customer context's own consumer reopens that
    customer as owner and keeper. Only a trade-in's car may say so (422
    `storno_return_not_trade_in`); without it the customer's history stays
    as the purchase wrote it.

    The caller loaded `item` with its row lock and checked If-Match.
    """

    if item.purchase_price is None:
        raise ConflictError(
            f"Stock item {item.stock_number} has no booked purchase to storno.",
            details={"reason": "purchase_not_booked", "stockItemId": str(item.id)},
        )
    if item.left_stock_at is not None:
        raise ConflictError(
            f"Stock item {item.stock_number} has left stock — its purchase can no longer be stornoed.",
            details={"reason": "purchase_storno_left_stock", "stockItemId": str(item.id)},
        )
    origin = pipeline_origin(item.pipeline_ref)
    if data.returned_to_customer and origin.get("originRole") != "trade_in":
        # KAN-572: only a trade-in has a customer it can go back to.
        raise UnprocessableEntityError(
            f"Stock item {item.stock_number} is not a trade-in — it cannot go back to a customer.",
            details={"reason": "storno_return_not_trade_in", "field": "returnedToCustomer", "stockItemId": str(item.id)},
        )
    invoiced_by = contract_with_invoice_requested(db, tenant_id=item.tenant_id, stock_item_id=item.id)
    if invoiced_by is not None:
        raise ConflictError(
            f"Contract {invoiced_by} has requested the invoice for stock item {item.stock_number} — "
            "its purchase can no longer be stornoed.",
            details={"reason": "purchase_storno_invoice_requested", "contractNumber": invoiced_by, "stockItemId": str(item.id)},
        )

    before = _purchase_facts(item)
    stornoed_purchase_date = item.purchase_date
    reverse_purchase_entries(db, item=item, occurred_at=utcnow(), actor_id=actor_id)

    item.supplier_name = None
    item.supplier_is_vat_registered = None
    item.purchase_price = None
    item.purchase_date = None
    item.purchase_invoice_ref = None
    item.landed_cost = None
    item.notional_input_tax_applicable = None
    item.notional_input_tax_rate = None
    item.notional_input_tax_amount = None
    item.notional_input_tax_overridden = False
    item.is_invoiceable = False
    # Back in pipeline (FR-I-05): no longer «An Lager», so its days in stock
    # stop; booking it again starts them anew.
    item.lifecycle_status = LifecycleStatus.PIPELINE
    item.in_stock_at = None
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="purchase_storno",
        actor_id=actor_id,
        before=before,
        after={**_purchase_facts(item), "returnedToCustomer": data.returned_to_customer},
        reason=data.reason,
    )
    publish(
        db,
        OutboxEvent(
            event_type="inventory.stock_item.storno",
            tenant_id=item.tenant_id,
            producer=_EVENT_PRODUCER,
            aggregate_type="stock_item",
            aggregate_id=item.id,
            payload={
                "stockNumber": item.stock_number,
                "vehicleId": str(item.vehicle_id) if item.vehicle_id else None,
                "stornoedBy": str(actor_id) if actor_id else None,
                # KAN-572 (additive): whether the car went back to the customer
                # who traded it in, the purchase date the storno cancelled, and
                # the trade-in's contract (`originContractId`/`originRole`, as
                # `inventory.stock_item.purchased` carries them) — what the
                # customer context needs to reopen that customer as holder.
                # KAN-632 (additive): the trade-in's seller and signature, as
                # the purchase event carries them (`trade_in_seller_fields`).
                "returnedToCustomer": data.returned_to_customer,
                "purchaseDate": stornoed_purchase_date.isoformat() if stornoed_purchase_date else None,
                **origin,
                **trade_in_seller_fields(item),
            },
        ),
    )
    db.commit()
    db.refresh(item)
    return item


def override_notional_input_tax(
    db: Session, *, item: StockItem, data: NotionalInputTaxOverrideRequest, actor_id: uuid.UUID | None
) -> StockItem:
    before = {
        "notionalInputTaxApplicable": item.notional_input_tax_applicable,
        "notionalInputTaxRate": str(item.notional_input_tax_rate) if item.notional_input_tax_rate else None,
        "notionalInputTaxAmount": str(item.notional_input_tax_amount) if item.notional_input_tax_amount else None,
    }

    item.notional_input_tax_applicable = data.applicable
    item.notional_input_tax_rate = data.rate if data.applicable else None
    item.notional_input_tax_amount = (
        _compute_notional_input_tax(purchase_price=item.purchase_price, rate=data.rate)
        if data.applicable and item.purchase_price is not None
        else None
    )
    item.notional_input_tax_overridden = True
    item.updated_by = actor_id
    item.version += 1
    db.flush()

    record_audit_event(
        db,
        entity_type="stock_item",
        entity_id=item.id,
        tenant_id=item.tenant_id,
        action="notional_input_tax_override",
        actor_id=actor_id,
        before=before,
        after={
            "notionalInputTaxApplicable": item.notional_input_tax_applicable,
            "notionalInputTaxRate": str(item.notional_input_tax_rate) if item.notional_input_tax_rate else None,
            "notionalInputTaxAmount": str(item.notional_input_tax_amount) if item.notional_input_tax_amount else None,
        },
        reason=data.reason,
    )
    db.commit()
    db.refresh(item)
    return item

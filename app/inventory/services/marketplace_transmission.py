"""Marketplace feed transmission (WP-7 PR-9, ADR-062, KAN-27).

`services/publishing.py`'s `publish()`/`unpublish()` only ever change
local INTENT (`StockItemPublishing.state`) — this module is the only
place that actually reaches a marketplace, and it is reached only from
the outbox consumer (`app/inventory/consumers.py`), never synchronously
from the publish/unpublish API call itself. Both events re-trigger the
exact same thing here: recompute and re-send the COMPLETE currently-
published set for that (tenant, channel), never a per-item diff.
AS24i's own full-delivery semantics (Schnittstellenbeschrieb v34 §4.2,
confirmed verbatim in `app/integration/adapters/autoscout24.py`'s own
docstring) are why: an object missing from a delivered file is DELETED
at the marketplace, so unpublish achieves its destructive effect for
free by simply not appearing in the next feed — there is no separate
"delete" call to make, and there must not be, since the two would drift.

THE SAFETY RULE THIS MODULE EXISTS TO ENFORCE ("a truncated set is never
sent" — KAN-27's own exit criterion 3): every currently-published item's
listing is BUILT and VALIDATED first, entirely locally, before anything
is sent. If validating even one fails, the ENTIRE transmission is
refused — nothing reaches the marketplace, the item(s) that failed are
marked `FAILED` with why, and every other item's `transmission_status` is
left exactly as it was. Sending a feed that silently dropped the one bad
item would unpublish it at the marketplace without telling anyone —
exactly the danger ADR-062 names.

TWO PHASES, TWO DIFFERENT GATEWAY ENTRY POINTS, DELIBERATELY:
validation (`app.integration.public.resolve_adapter`) does no provider
I/O at all, so it must never touch `call_capability`'s call-log/circuit-
breaker machinery — that machinery exists to track the CONNECTION's own
health, and a dealer's own missing colour or body style is not a
connection problem. Only once every listing has validated cleanly does
this module enter `call_capability` at all, for the one real network
call (`transmit_feed`) that actually reaches AS24i. An earlier version of
this module ran validation INSIDE `call_capability`'s own `with` block
and `return`ed early on a validation failure — a `return` from inside a
`with` block is normal completion, not an exception, so `call_capability`
recorded that as a SUCCESSFUL call and reset the circuit breaker's
failure count even though zero bytes had been sent. Splitting the two
phases across `resolve_adapter` and `call_capability` closes that gap:
nothing is logged, and no circuit state changes, unless a real
transmission was actually attempted.
"""

import uuid
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.integration.public import (
    CircuitOpenError,
    MarketplaceAdapter,
    MarketplaceListing,
    MarketplaceListingError,
    MarketplaceTransmissionError,
    ProviderGatewayError,
    call_capability,
    get_enabled_connection,
    resolve_adapter,
)
from app.inventory.models.stock_item import StockItem
from app.inventory.models.stock_item_publishing import (
    MarketplaceChannel,
    PublishingState,
    StockItemPublishing,
    TransmissionStatus,
)

_CAPABILITY = "marketplace_publish"

# Any of these means "the connection itself refused before a real call
# was even attempted" — caught identically at both the validation-adapter
# resolution step and the real transmission step, in both cases marking
# every currently-published row FAILED rather than propagating and
# crashing the outbox consumer.
_GATEWAY_REFUSAL_ERRORS = (ProviderGatewayError, CircuitOpenError)


def _resolve_price(item: StockItem) -> Decimal:
    # NOT `effective_price or list_price or Decimal(0)` — Decimal("0.00")
    # is falsy in Python, so that would silently substitute list_price
    # for a listing whose real, dealer-entered price genuinely is zero.
    # compute_blocking_conditions (services/publishing.py) already uses
    # the correct `is None` check for the same fact; this mirrors it.
    if item.effective_price is not None:
        return item.effective_price
    if item.list_price is not None:
        return item.list_price
    return Decimal(0)


def _build_listing(item: StockItem) -> MarketplaceListing:
    # `vehicle_label` is the only make/model text a StockItem carries
    # ("Volkswagen Käfer 1303 LS Cabriolet") — neither it nor VehicleMdm
    # (the only thing vehicle_id points to) has a structured brand/model
    # split. This naive first-word split is a best-effort approximation,
    # not a real resolution — it is wrong for multi-word brands (Alfa
    # Romeo, Mercedes-Benz, Land Rover). A real fix needs a genuine
    # brand/model breakdown somewhere in inventory or vehicle, which is
    # its own follow-up, not invented here.
    make, _, model = item.vehicle_label.partition(" ")
    return MarketplaceListing(
        stock_item_id=str(item.id),
        make=make,
        model=model,
        body_style=item.body_style,
        condition=item.condition.value,
        exterior_colour=item.exterior_colour or "",
        # Passed through as-is, including None — a missing odometer
        # reading must fail validate_listing the same way a missing
        # colour does, never silently transmit "0 km" as a real fact.
        odometer_km=item.odometer_km,
        price=_resolve_price(item),
        first_registration_date=item.first_registration_date,
        model_year=None,
        image_urls=[],
    )


def _mark(rows: list[StockItemPublishing], *, status: TransmissionStatus, error: str | None, now) -> None:
    for row in rows:
        row.transmission_status = status
        row.last_transmission_error = error
        row.last_attempted_at = now


def assemble_and_transmit(db: Session, *, tenant_id: uuid.UUID, channel: MarketplaceChannel) -> None:
    rows = list(
        db.scalars(
            select(StockItemPublishing).where(
                StockItemPublishing.tenant_id == tenant_id,
                StockItemPublishing.channel == channel,
                StockItemPublishing.state == PublishingState.PUBLISHED,
            )
        ).all()
    )
    now = utcnow()

    connection = get_enabled_connection(db, tenant_id=tenant_id, provider_code=channel.value)
    if connection is None:
        # Genuinely no connection to try — true for a dealer who hasn't
        # connected this channel yet, and (since no IntegrationProvider
        # row is ever seeded for carmarket/autolina — see
        # autoscout24.py's own docstring) permanently true for those two
        # channels until their own specifications are obtained. A
        # zero-row `rows` (nothing currently published) is a legitimate
        # no-op here, not an error — there is nothing to mark.
        _mark(rows, status=TransmissionStatus.FAILED,
              error=f"No {channel.value} connection configured for this dealership.", now=now)
        db.commit()
        return

    items_by_id = {
        item.id: item
        for item in db.scalars(select(StockItem).where(StockItem.id.in_(r.stock_item_id for r in rows))).all()
    }

    # -- phase 1: validate every listing locally — no provider I/O, so no
    # call_capability, no call-log row, no circuit-breaker involvement.
    try:
        adapter = cast(MarketplaceAdapter, resolve_adapter(db, connection))
    except _GATEWAY_REFUSAL_ERRORS as exc:
        _mark(rows, status=TransmissionStatus.FAILED, error=str(exc), now=now)
        db.commit()
        return

    listings: list[MarketplaceListing] = []
    failed_rows: list[StockItemPublishing] = []
    failure_messages: dict[uuid.UUID, str] = {}
    for row in rows:
        item = items_by_id.get(row.stock_item_id)
        if item is None:
            failed_rows.append(row)
            failure_messages[row.id] = "Stock item no longer exists."
            continue
        listing = _build_listing(item)
        try:
            adapter.validate_listing(listing)
        except MarketplaceListingError as exc:
            failed_rows.append(row)
            failure_messages[row.id] = f"{exc.field}: {exc}"
            continue
        listings.append(listing)

    if failed_rows:
        # Refuse the WHOLE feed — a listing that validated cleanly is not
        # sent either. Only the failing item(s) are marked; a previously-
        # TRANSMITTED item that still validates fine keeps that status,
        # since the marketplace's own last-known-good feed is still live
        # and still correct for it. transmit_feed is never called, and
        # call_capability is never entered — nothing to log.
        _mark(failed_rows, status=TransmissionStatus.FAILED, error=None, now=now)
        for row in failed_rows:
            row.last_transmission_error = failure_messages[row.id]
        db.commit()
        return

    # -- phase 2: every listing validated — the one real network call,
    # through call_capability so it is logged and counted toward this
    # connection's own health, success or failure.
    try:
        with call_capability(db, connection=connection, capability=_CAPABILITY) as resolved_adapter:
            cast(MarketplaceAdapter, resolved_adapter).transmit_feed(listings)
    except (*_GATEWAY_REFUSAL_ERRORS, MarketplaceTransmissionError) as exc:
        # Mark FAILED for visibility (a dealer/support seeing the row
        # mid-retry should not see it as still PENDING), then RE-RAISE —
        # unlike a validation refusal (permanent until a human fixes the
        # data) or a missing connection (permanent until one is
        # configured), everything reaching this except clause is by
        # definition about the CONNECTION, at a moment every listing had
        # already validated cleanly. That is exactly the kind of transient
        # failure the outbox's own retry/backoff/dead-letter mechanism
        # (app/core/outbox_worker.py, MAX_ATTEMPTS=5) exists for — catching
        # it here and returning normally would tell consume_once nothing
        # went wrong, permanently skipping any retry.
        _mark(rows, status=TransmissionStatus.FAILED, error=str(exc), now=now)
        db.commit()
        raise

    _mark(rows, status=TransmissionStatus.TRANSMITTED, error=None, now=now)
    db.commit()

"""MarketplaceAdapter (KAN-27, WP-7, ADR-062) — the shape every
marketplace channel implements. Deliberately NOT `ProviderAdapter`
(adapters/base.py): that Protocol's methods are vehicle-data FETCH verbs
(`fetch_options`, `fetch_colours`, ...); a marketplace channel does the
reverse — it is handed a tenant's already-assembled listing data and
DELIVERS it outward. Both kinds of adapter still go through the exact
same `services/gateway.py::call_capability` (credential resolution,
circuit breaker, call-log row) — that machinery is already
transport-agnostic, it just needed a second Protocol shaped for the
opposite direction of travel.

`MarketplaceListing` is the generic, channel-agnostic input a caller
(app/inventory/services/marketplace_transmission.py) builds once per
stock item — the adapter's own job is to map these plain facts into its
channel's wire format (AS24i's own field names/codes for
`AutoScout24Adapter`), the same "adapter owns the encoding, caller never
sees a provider code" split `ProviderAdapter` already uses, just in the
outbound direction.

`transmit_feed` takes the COMPLETE set of listings that should be live —
never a diff, never one item at a time. AS24i's own full-delivery
semantics (Schnittstellenbeschrieb v34 §4.2) are the reason: an object
missing from a delivered file is DELETED at the marketplace, so "publish
one, then separately unpublish another" has no meaning at this layer —
there is only "here is everything that should be live right now."
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class MarketplaceListing:
    stock_item_id: str
    make: str
    model: str
    body_style: str | None
    condition: str  # "new" | "used" | "demo" | "tagesz" — StockItemCondition.value, unresolved (adapter's job)
    exterior_colour: str
    # Nullable rather than defaulted to 0 by the caller — a missing
    # odometer reading (e.g. a value cleared after publish, on a resend)
    # must fail validate_listing the same way a missing colour does,
    # never silently transmit "0 km" as if that were a real fact.
    odometer_km: int | None
    price: Decimal
    first_registration_date: dt.date | None
    model_year: int | None
    image_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TransmissionResult:
    """Every listing handed to `transmit_feed` is expected to succeed
    together — a channel that can only tell us about a partial failure
    (unlike AS24i's own all-or-nothing FTP drop) would report it here,
    but no adapter in this codebase does yet."""

    transmitted_count: int


class MarketplaceTransmissionError(Exception):
    """Raised by an adapter's `transmit_feed` on any failure to deliver
    the file at all (connection refused, auth rejected, ...). Never
    raised for a per-listing data problem — that is caught earlier, by
    `marketplace_transmission.py`'s own build pass, precisely so a
    delivery is attempted only once every listing has already built
    cleanly."""


class MarketplaceListingError(Exception):
    """One listing's own facts can't be mapped into a valid row for this
    channel — e.g. a mandatory field genuinely empty. Raised per-listing,
    by an adapter's own listing-building code (never inside
    `transmit_feed` itself), so the caller
    (`app/inventory/services/marketplace_transmission.py`) can abort the
    WHOLE feed before anything is sent, per ADR-062's full-delivery
    danger. Exported via `app.integration.public` — never
    `app.integration.adapters.*` directly — so inventory's own catch
    clause never needs a per-channel import."""

    def __init__(self, *, stock_item_id: str, field: str, message: str) -> None:
        self.stock_item_id = stock_item_id
        self.field = field
        super().__init__(f"{stock_item_id}: {field}: {message}")


class MarketplaceAdapter(Protocol):
    def validate_listing(self, listing: MarketplaceListing) -> None:
        """Raises `MarketplaceListingError` if this ONE listing cannot be
        mapped into a valid row for this channel. No I/O — implementations
        must not open a connection or resolve a credential here. Called
        once per currently-published item, on an adapter resolved via
        `app.integration.public.resolve_adapter` (never `call_capability`)
        — a per-listing data problem must never reach the gateway's own
        call-log/circuit-breaker machinery, which exists to track the
        CONNECTION's health, not the caller's own data, and `resolve_adapter`
        carries none of that machinery."""
        ...

    def transmit_feed(self, listings: list[MarketplaceListing]) -> TransmissionResult:
        """Assumes every listing already passed `validate_listing` —
        never re-validates. Raises `MarketplaceTransmissionError` (or lets
        a lower-level connection error propagate) only for a failure to
        deliver the file itself."""
        ...

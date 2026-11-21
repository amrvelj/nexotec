"""The Google Business Profile push (KAN-226 PR-3; Dealer Administration
FR-A-35, D-A-32; Anto's Q1 A): Nexotec → Google, one way. Driven by facts,
never by polling (rules 4–6):

- `platform.location.updated`, `platform.location.schedule_changed` and
  Integration's own `integration.gbp_location_binding.activated` push the
  location's fields (`push_location`);
- `platform.publishing_profile.updated` naming `imagery`, and the activation,
  upload the location's publishing images not yet uploaded (`sync_photos`).

Each handler reads the location's current state through
`platform.public.location_channel_facts` — never the payload, since delivery
is at-least-once and unordered — and runs under its consumer's
`processed_event` row (`app/core/consumer.py`), so a redelivered event is a
no-op.

**The mapping (FR-A-35, documented here and in the ADR):**

- the main department's hours → `regularHours`. The main department is
  `sales` when the site opens for sales on any day, else `service`, else
  `parts` (a workshop-only site shows its service hours);
- every other department with hours of its own → `moreHours`, under the
  `hoursTypeId` Google's category for the location offers for it
  (`HOURS_TYPE_CANDIDATES`, read from the Google location's
  `categories.*.moreHoursTypes` when there is such a department). A
  department whose hours equal the main department's is not repeated; one
  Google offers no type for is not sent and is kept in
  `integration_gbp_push_state.unmapped_departments` for PR-4's drift card;
- each open weekday → one period per interval (two with a lunch break),
  `openDay`/`closeDay` the weekday, `openTime`/`closeTime` a `TimeOfDay`
  (00:00 is sent as `{}`, Google's documented zero value);
- the site's closures and its holiday calendar's observed holidays, from
  today for a year (`platform.public`) → `specialHours`, one closed day
  per period (Nexotec stores no special opening hours, only closed days);
- the address block → `storefrontAddress` (`regionCode` the country, CH
  by default; `addressLines` street and number — at most five lines);
  the phone → `phoneNumbers.primaryPhone`; the Betrieb's website →
  `websiteUri` (`https://` added to a bare domain).

A field Nexotec holds no value for is left out of the `updateMask`, so the
push never clears what the dealer keeps at Google. An inactive location is
not pushed. Everything sent is in the adapter's closed `PATCHABLE_FIELDS`.

**The debounce, per location:** the binding row is locked (`FOR UPDATE`),
and the facts the push reads are hashed. When the hash equals the last
successful push's (`integration_gbp_push_state.source_hash`) nothing is
sent — so a burst of facts about one location is one push of its current
state, whichever event of the burst is handled first, and no Google call is
made for the rest.

**Failures:** a refused grant moves the binding to `error`
(`gbp_link.mark_grant_refused`) and the event is consumed — nothing is
retried until the manager reconnects. Everything else (a transport failure,
a timeout, a 5xx, an open breaker, Google refusing the request, Nexotec's
client missing) raises: the outbox worker retries with backoff and
dead-letters with its alert after the last attempt (rule 10).
"""

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.outbox_model import OutboxMessage
from app.core.relative_dates import today_in_session_zone
from app.integration.adapters.google_business_profile import GBP_CAPABILITY, GoogleBusinessProfileAdapter
from app.integration.errors import ProviderAccountRefusedError, ProviderConfigurationError
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding, GbpPhoto, GbpPushState
from app.integration.services import gbp_link
from app.integration.services.gateway import call_capability
from app.platform.public import (
    ChannelOpeningHours,
    LocationChannelFacts,
    location_channel_facts,
    location_channel_image,
)

logger = logging.getLogger(__name__)

_PURPOSE = "business_profile_push"
IMAGERY = "imagery"
DEPARTMENTS = ("sales", "service", "parts")
MAX_ADDRESS_LINES = 5
CATEGORY_READ_MASK = ("categories",)

# Google's `hoursTypeId`s a Nexotec department may be shown under, in order of
# preference. Google's list per category is what decides: a department is sent
# only under a type the location's category offers. Not yet checked against a
# real car-dealer category (no Business Profile API access): the drift card
# (PR-4) names every department left out.
HOURS_TYPE_CANDIDATES: Mapping[str, tuple[str, ...]] = {
    "sales": ("SALES", "BUSINESS"),
    "service": ("SERVICE", "AUTO_SERVICE", "REPAIR"),
    "parts": ("PARTS", "AUTO_PARTS"),
}

_DAY_NAMES = {1: "MONDAY", 2: "TUESDAY", 3: "WEDNESDAY", 4: "THURSDAY", 5: "FRIDAY", 6: "SATURDAY", 7: "SUNDAY"}

# A publishing image's kind → the Business Profile photo category. The
# primary hero image is the cover; another hero image an additional photo.
PHOTO_CATEGORY_BY_KIND: Mapping[str, str] = {"premises": "EXTERIOR", "team": "TEAMS", "hero": "ADDITIONAL"}
COVER_CATEGORY = "COVER"


# -- the mapping (pure) -------------------------------------------------------------------


def _time_of_day(value: dt.time) -> dict[str, int]:
    out: dict[str, int] = {}
    if value.hour:
        out["hours"] = value.hour
    if value.minute:
        out["minutes"] = value.minute
    return out  # 00:00 is `{}`


def _periods(rows: list[ChannelOpeningHours]) -> list[dict[str, Any]]:
    periods: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: r.weekday):
        day = _DAY_NAMES[row.weekday]
        intervals = [(row.opens_at, row.closes_at)]
        if row.second_opens_at is not None and row.second_closes_at is not None:
            intervals.append((row.second_opens_at, row.second_closes_at))
        for opens, closes in intervals:
            periods.append(
                {
                    "openDay": day,
                    "openTime": _time_of_day(opens),
                    "closeDay": day,
                    "closeTime": _time_of_day(closes),
                }
            )
    return periods


def _by_department(facts: LocationChannelFacts) -> dict[str, list[ChannelOpeningHours]]:
    grouped: dict[str, list[ChannelOpeningHours]] = {}
    for row in facts.opening_hours:
        grouped.setdefault(row.department, []).append(row)
    return grouped


def main_department(facts: LocationChannelFacts) -> str | None:
    grouped = _by_department(facts)
    return next((d for d in DEPARTMENTS if grouped.get(d)), None)


def other_departments(facts: LocationChannelFacts) -> list[str]:
    """The departments to send as more hours: with hours of their own that
    differ from the main department's."""

    grouped = _by_department(facts)
    main = main_department(facts)
    if main is None:
        return []
    main_periods = _periods(grouped[main])
    return [d for d in DEPARTMENTS if d != main and grouped.get(d) and _periods(grouped[d]) != main_periods]


def more_hours_types(location_body: Mapping[str, Any]) -> frozenset[str]:
    """The `hoursTypeId`s Google offers for the location's categories."""

    categories = location_body.get("categories")
    if not isinstance(categories, dict):
        return frozenset()
    listed = [categories.get("primaryCategory"), *(categories.get("additionalCategories") or [])]
    found: set[str] = set()
    for category in listed:
        if not isinstance(category, dict):
            continue
        for item in category.get("moreHoursTypes") or []:
            if isinstance(item, dict) and isinstance(item.get("hoursTypeId"), str):
                found.add(item["hoursTypeId"])
    return frozenset(found)


def _website(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    value = value.strip()
    return value if value.lower().startswith(("http://", "https://")) else f"https://{value}"


def _address(facts: LocationChannelFacts) -> dict[str, Any] | None:
    street = " ".join(p for p in (facts.address_street, facts.address_house_number) if p and p.strip())
    if not (street and facts.address_postal_code and facts.address_locality):
        return None  # an incomplete address is never sent: Google would reject or misplace it
    address: dict[str, Any] = {
        "regionCode": (facts.address_country or "CH").upper(),
        "postalCode": facts.address_postal_code,
        "locality": facts.address_locality,
        "addressLines": [street][:MAX_ADDRESS_LINES],
    }
    if facts.address_canton:
        address["administrativeArea"] = facts.address_canton
    return address


def _special_hours(closed_days: tuple[dt.date, ...]) -> dict[str, Any]:
    return {
        "specialHourPeriods": [
            {"startDate": {"year": day.year, "month": day.month, "day": day.day}, "closed": True} for day in closed_days
        ]
    }


@dataclasses.dataclass(frozen=True)
class LocationPatch:
    fields: dict[str, Any]
    update_mask: tuple[str, ...]
    unmapped_departments: tuple[str, ...]


def build_patch(facts: LocationChannelFacts, *, offered_hours_types: frozenset[str]) -> LocationPatch:
    """The Location body and its mask for the facts; pure, so every case is
    tested without a fake Google."""

    fields: dict[str, Any] = {}
    mask: list[str] = []
    address = _address(facts)
    if address is not None:
        fields["storefrontAddress"] = address
        mask.append("storefrontAddress")
    if facts.phone and facts.phone.strip():
        fields["phoneNumbers"] = {"primaryPhone": facts.phone.strip()}
        mask.append("phoneNumbers.primaryPhone")
    website = _website(facts.website)
    if website is not None:
        fields["websiteUri"] = website
        mask.append("websiteUri")
    grouped = _by_department(facts)
    main = main_department(facts)
    unmapped: list[str] = []
    if main is not None:
        fields["regularHours"] = {"periods": _periods(grouped[main])}
        mask.append("regularHours")
        more: list[dict[str, Any]] = []
        for department in other_departments(facts):
            type_id = next((t for t in HOURS_TYPE_CANDIDATES[department] if t in offered_hours_types), None)
            if type_id is None:
                unmapped.append(department)
                continue
            more.append({"hoursTypeId": type_id, "periods": _periods(grouped[department])})
        if more:
            fields["moreHours"] = more
            mask.append("moreHours")
    if facts.closed_days:
        fields["specialHours"] = _special_hours(facts.closed_days)
        mask.append("specialHours")
    return LocationPatch(fields=fields, update_mask=tuple(mask), unmapped_departments=tuple(unmapped))


def source_hash(facts: LocationChannelFacts, *, google_location_name: str) -> str:
    """The hash of what a push reads — the debounce's key."""

    pushed = dataclasses.asdict(facts)
    pushed.pop("images")  # photos are `sync_photos`' own
    blob = json.dumps({"target": google_location_name, "facts": pushed}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# -- the handlers ------------------------------------------------------------------------


def _ids(message: OutboxMessage) -> tuple[uuid.UUID, uuid.UUID | None]:
    dealership_id = uuid.UUID(message.payload["dealershipId"])
    if message.tenant_id is not None and message.tenant_id != dealership_id:
        raise ValueError(f"{message.event_type} message {message.id} names two businesses.")
    location = message.payload.get("locationId")
    return dealership_id, uuid.UUID(location) if location else None


def _live_binding(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID) -> GbpLocationBinding | None:
    """The location's active binding, locked: one push per location at a time."""

    return db.scalar(
        select(GbpLocationBinding)
        .where(
            GbpLocationBinding.tenant_id == dealership_id,
            GbpLocationBinding.location_id == location_id,
            GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
            GbpLocationBinding.google_location_name.is_not(None),
        )
        .with_for_update()
    )


def _state(db: Session, binding: GbpLocationBinding) -> GbpPushState:
    state = db.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))
    if state is None:
        state = GbpPushState(
            binding_id=binding.id, tenant_id=binding.tenant_id, pushed_fields=[], unmapped_departments=[]
        )
        db.add(state)
    return state


@contextmanager
def _google(db: Session) -> Iterator[GoogleBusinessProfileAdapter]:
    connection = gbp_link.client_connection(db)
    if connection is None:
        # Raised, so the worker retries and dead-letters with its alert: a
        # live binding without Nexotec's client is broken setup (rule 10).
        raise ProviderConfigurationError("Nexotec's Google Business Profile client is not set up.")
    with call_capability(db, connection=connection, capability=GBP_CAPABILITY, purpose=_PURPOSE) as raw:
        yield cast(GoogleBusinessProfileAdapter, raw)


def _facts(db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID) -> LocationChannelFacts | None:
    today, _ = today_in_session_zone()
    return location_channel_facts(db, dealership_id=dealership_id, location_id=location_id, today=today)


def push_location(db: Session, message: OutboxMessage) -> bool:
    """Pushes the location's fields when they changed since the last push.
    True when Google was written to."""

    dealership_id, location_id = _ids(message)
    if location_id is None:
        return False
    binding = _live_binding(db, dealership_id=dealership_id, location_id=location_id)
    if binding is None:
        return False
    facts = _facts(db, dealership_id, location_id)
    if facts is None or not facts.is_active:
        return False
    google_location = cast(str, binding.google_location_name)
    digest = source_hash(facts, google_location_name=google_location)
    state = _state(db, binding)
    if state.source_hash == digest:
        return False  # the debounce: this state is what Google already has from us
    try:
        with _google(db) as adapter:
            token = adapter.access_token(binding_id=binding.id)
            offered: frozenset[str] = frozenset()
            if other_departments(facts):
                read = adapter.get_location(
                    access_token=token, location_name=google_location, read_mask=CATEGORY_READ_MASK
                )
                offered = more_hours_types(read.body)
            patch = build_patch(facts, offered_hours_types=offered)
            if patch.update_mask:
                adapter.patch_location(
                    access_token=token,
                    location_name=google_location,
                    fields=patch.fields,
                    update_mask=patch.update_mask,
                )
    except ProviderAccountRefusedError:
        # The binding's grant, never the client's: the manager reconnects.
        gbp_link.mark_grant_refused(db, binding)
        return False
    state.source_hash = digest
    state.pushed_at = utcnow()
    state.pushed_fields = list(patch.update_mask)
    state.unmapped_departments = list(patch.unmapped_departments)
    return bool(patch.update_mask)


def _photo_category(kind: str, *, is_primary: bool) -> str:
    if kind == "hero" and is_primary:
        return COVER_CATEGORY
    return PHOTO_CATEGORY_BY_KIND[kind]


def sync_photos(db: Session, message: OutboxMessage) -> int:
    """Uploads the location's publishing images this binding has not
    received yet; returns how many. Each upload commits its own row, so a
    retry after a failure never uploads an image twice."""

    if message.event_type != BINDING_ACTIVATED and IMAGERY not in (message.payload.get("changedFields") or []):
        return 0
    dealership_id, location_id = _ids(message)
    if location_id is None:
        return 0
    binding = _live_binding(db, dealership_id=dealership_id, location_id=location_id)
    if binding is None:
        return 0
    facts = _facts(db, dealership_id, location_id)
    if facts is None or not facts.is_active:
        return 0
    uploaded = set(db.scalars(select(GbpPhoto.image_id).where(GbpPhoto.binding_id == binding.id)).all())
    pending = [image for image in facts.images if image.image_id not in uploaded]
    if not pending:
        return 0
    binding_id, tenant_id = binding.id, binding.tenant_id
    account, google_location = cast(str, binding.google_account_name), cast(str, binding.google_location_name)
    count = 0
    try:
        with _google(db) as adapter:
            token = adapter.access_token(binding_id=binding_id)
            for image in pending:
                stored = location_channel_image(
                    db, dealership_id=dealership_id, location_id=location_id, image_id=image.image_id
                )
                if stored is None:
                    logger.warning(
                        "business profile photo not stored; skipped",
                        extra={"bindingId": str(binding_id), "imageId": str(image.image_id)},
                    )
                    continue
                content, content_type = stored
                category = _photo_category(image.kind, is_primary=image.is_primary)
                media = adapter.upload_photo(
                    access_token=token,
                    account_name=account,
                    location_name=google_location,
                    content=content,
                    content_type=content_type,
                    category=category,
                )
                db.add(
                    GbpPhoto(
                        binding_id=binding_id,
                        tenant_id=tenant_id,
                        image_id=image.image_id,
                        category=category,
                        google_media_name=media.name[:512],
                        uploaded_at=utcnow(),
                    )
                )
                db.commit()
                count += 1
                # The commit ended the binding's lock: take it again.
                if _live_binding(db, dealership_id=dealership_id, location_id=location_id) is None:
                    break
    except ProviderAccountRefusedError:
        refused = db.get(GbpLocationBinding, binding_id)
        if refused is not None:
            gbp_link.mark_grant_refused(db, refused)
    return count


BINDING_ACTIVATED = gbp_link.BINDING_ACTIVATED_EVENT

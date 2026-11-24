"""The Google Business Profile push (KAN-226 PR-3; Dealer Administration
FR-A-35, D-A-32; Anto's Q1 A): Nexotec → Google, one way. Driven by facts,
never by polling (rules 4–6):

`platform.location.updated`, `platform.location.schedule_changed` and
Integration's own `integration.gbp_location_binding.activated` push the
location's fields (`push_location`); `platform.dealership.updated` (the
Betrieb's website changed, KAN-785) pushes each of its bound locations
(`push_dealership_locations`). One fact is the calendar's, not a record's:
the closed days slide with the date, so a holiday more than a year away
enters the horizon on a day no event fires. The nightly
`run_horizon_pushes` (KAN-793) therefore offers every active binding to the
same push: the debounce sends nothing unless today's facts differ from the
last push's, which in practice is the day a closed day enters or leaves the
year. Photos are not pushed here: the worker
cannot read the object store's disk in production (render.yaml, ADR-081 as
amended, KAN-738), so the web service uploads them (`gbp_photos`, KAN-784,
ADR-209).

The handler reads the location's current state through
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

**What is cleared at Google:** a field Nexotec never pushed and holds no
value for is left out of the `updateMask`, so the push never clears what the
dealer keeps at Google himself. A field the last successful push sent
(`integration_gbp_push_state.pushed_fields`) and that is empty now — the last
closure deleted, a department that lost its own hours or now has the main
department's, the phone or the website removed, every opening hour removed —
is in the mask with an empty value (`specialHours: {}`, `moreHours: []`,
`regularHours: {}`, `phoneNumbers: {}`, `websiteUri` left out of the body), so
Google clears it: a removal in Nexotec reaches Google. The address is never
cleared: an address that became incomplete is not sent, and Google keeps the
last one (a storefront without an address is refused there). An inactive
location is not pushed. Everything sent is in the adapter's closed `PATCHABLE_FIELDS`.

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
from app.integration.models.google_business_profile import GbpBindingStatus, GbpLocationBinding, GbpPushState
from app.integration.services import gbp_link
from app.integration.services.gateway import call_capability
from app.platform.public import ChannelOpeningHours, LocationChannelFacts, location_channel_facts

logger = logging.getLogger(__name__)

_PURPOSE = "business_profile_push"
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

# The fields Google clears when they are in the mask with an empty value —
# every pushed field but the address (see the module docstring).
CLEARABLE_FIELDS: Mapping[str, Any] = {
    "phoneNumbers.primaryPhone": ("phoneNumbers", {}),
    "websiteUri": None,  # in the mask, absent from the body
    "regularHours": ("regularHours", {}),
    "moreHours": ("moreHours", []),
    "specialHours": ("specialHours", {}),
}


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
    pushed_fields: tuple[str, ...] = ()


def build_patch(
    facts: LocationChannelFacts,
    *,
    offered_hours_types: frozenset[str],
    previously_pushed: frozenset[str] = frozenset(),
) -> LocationPatch:
    """The Location body and its mask for the facts; pure, so every case is
    tested without a fake Google. `previously_pushed` is the last successful
    push's mask: a field in it that is empty now is cleared at Google.
    `LocationPatch.pushed_fields` names the fields sent with a value."""

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
    valued = tuple(mask)
    for path, empty in CLEARABLE_FIELDS.items():
        if path in previously_pushed and path not in valued:
            mask.append(path)
            if empty is not None:
                fields[empty[0]] = type(empty[1])()
    return LocationPatch(
        fields=fields, update_mask=tuple(mask), unmapped_departments=tuple(unmapped), pushed_fields=valued
    )


def source_hash(facts: LocationChannelFacts, *, google_location_name: str) -> str:
    """The hash of what a push reads — the debounce's key."""

    pushed = dataclasses.asdict(facts)
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


def _facts(
    db: Session, dealership_id: uuid.UUID, location_id: uuid.UUID, *, today: dt.date | None = None
) -> LocationChannelFacts | None:
    if today is None:
        today, _ = today_in_session_zone()
    return location_channel_facts(db, dealership_id=dealership_id, location_id=location_id, today=today)


def push_location(db: Session, message: OutboxMessage) -> bool:
    """Pushes the location's fields when they changed since the last push.
    True when Google was written to."""

    dealership_id, location_id = _ids(message)
    if location_id is None:
        return False
    return _push(db, dealership_id=dealership_id, location_id=location_id)


def push_dealership_locations(db: Session, message: OutboxMessage) -> int:
    """KAN-785: a Betrieb-level fact (`platform.dealership.updated`, its
    website changed) pushes every one of its locations with a live binding,
    each through the same debounce, so a redelivery after the commit sends
    nothing again. The pushes commit together with the processed-event row:
    a retry after one location failed sends the earlier ones once more — the
    same PATCH of the same state, which Google applies as a no-op.
    Returns how many locations Google was written to."""

    dealership_id, _ = _ids(message)
    location_ids = db.scalars(
        select(GbpLocationBinding.location_id)
        .where(
            GbpLocationBinding.tenant_id == dealership_id,
            GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
            GbpLocationBinding.google_location_name.is_not(None),
        )
        .order_by(GbpLocationBinding.location_id)
    ).all()
    return sum(_push(db, dealership_id=dealership_id, location_id=location_id) for location_id in location_ids)


def _push(db: Session, *, dealership_id: uuid.UUID, location_id: uuid.UUID, today: dt.date | None = None) -> bool:
    binding = _live_binding(db, dealership_id=dealership_id, location_id=location_id)
    if binding is None:
        return False
    facts = _facts(db, dealership_id, location_id, today=today)
    if facts is None or not facts.is_active:
        return False
    google_location = cast(str, binding.google_location_name)
    digest = source_hash(facts, google_location_name=google_location)
    state = _state(db, binding)
    if state.source_hash == digest:
        return False  # the debounce: this state is what Google already has from us
    try:
        with _google(db) as adapter:
            # A token only when Google is called (KAN-788): a patch whose mask
            # is empty sends nothing, so it asks the token endpoint nothing.
            token: str | None = None
            offered: frozenset[str] = frozenset()
            if other_departments(facts):
                token = adapter.access_token(binding_id=binding.id)
                read = adapter.get_location(
                    access_token=token, location_name=google_location, read_mask=CATEGORY_READ_MASK
                )
                offered = more_hours_types(read.body)
            patch = build_patch(
                facts, offered_hours_types=offered, previously_pushed=frozenset(state.pushed_fields or [])
            )
            if patch.update_mask:
                adapter.patch_location(
                    access_token=token or adapter.access_token(binding_id=binding.id),
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
    state.pushed_fields = list(patch.pushed_fields)
    state.unmapped_departments = list(patch.unmapped_departments)
    return bool(patch.update_mask)


class HorizonPushFailedError(RuntimeError):
    """Raised by the nightly horizon push after the last binding when one
    or more could not be pushed, so the daily scheduler retries the job and
    alarms (rule 10). The bindings pushed before it stay committed."""


def run_horizon_pushes(db: Session, *, today: dt.date | None = None) -> dict[str, int]:
    """KAN-793 — offers every active binding of every Betrieb to the push
    with today's facts, so a closed day that entered the one-year horizon
    reaches Google without any location change (ADR-199: «closed days slide
    daily»). The debounce decides: nothing is sent for a location whose facts
    are the ones Google already has from us. Each binding commits on its own;
    a refused grant moves the binding to `error` as in the consumer (counted
    `grantRefused`). Any other failure is logged per binding at WARNING, the
    rest still pushed, and the job raises at the end
    (`HorizonPushFailedError`, naming the locations) to be retried the same
    day: the daily scheduler logs the episode's first failure at ERROR and
    its retries at WARNING (KAN-303), so a broken location alarms once, not
    on every retry. Registered before the drift check, which compares the
    full horizon; when this job failed for a location, that night's check
    reports its special hours as drift (true: Google lacks the days), and a
    retry that pushes later in the day leaves that row until the next night."""

    counts = {"pushed": 0, "unchanged": 0, "grantRefused": 0, "failed": 0}
    if gbp_link.client_connection(db) is None:
        logger.info("business profile horizon push: Nexotec's client is not set up; nothing pushed")
        return counts
    if today is None:
        today, _ = today_in_session_zone()
    rows = db.execute(
        select(GbpLocationBinding.id, GbpLocationBinding.tenant_id, GbpLocationBinding.location_id)
        .where(
            GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
            GbpLocationBinding.google_location_name.is_not(None),
        )
        .order_by(GbpLocationBinding.tenant_id, GbpLocationBinding.location_id)
    ).all()
    db.rollback()
    failed: list[str] = []
    for binding_id, dealership_id, location_id in rows:
        try:
            pushed = _push(db, dealership_id=dealership_id, location_id=location_id, today=today)
            refused = (
                not pushed
                and db.scalar(select(GbpLocationBinding.status).where(GbpLocationBinding.id == binding_id))
                == GbpBindingStatus.ERROR
            )
            db.commit()
        except Exception:
            db.rollback()
            failed.append(str(location_id))
            logger.warning(
                "business profile horizon push failed for one location",
                exc_info=True,
                extra={"dealershipId": str(dealership_id), "locationId": str(location_id)},
            )
            continue
        counts["pushed" if pushed else "grantRefused" if refused else "unchanged"] += 1
    counts["failed"] = len(failed)
    if failed:
        raise HorizonPushFailedError(
            f"{len(failed)} Business Profile location(s) could not be pushed: {', '.join(failed)}."
        )
    return counts

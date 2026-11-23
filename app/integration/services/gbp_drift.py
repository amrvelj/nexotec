"""The nightly Google Business Profile drift check (KAN-226 PR-4; Dealer
Administration FR-A-35, D-A-32; ADR-199).

The channel is one way, Nexotec → Google (PR-3's push). A field changed at
Google — by the dealer, by Google's own suggestions, by a user edit Google
accepted — is never pulled back. This job **reports** it: every night it reads
each active binding's Google location back and compares it, field by field,
with what the push would send now. It never repairs and never pushes; the
result is one `integration_gbp_drift_check` row per binding, which the drift
card (`GET …/business-profile/bindings`) and the status page
(`GbpDriftStatusProvider`) read.

**One definition of «what we send».** The expected values are
`gbp_push.build_patch` of the location's current facts (the same
`platform.public.location_channel_facts` read, the same `previously_pushed`
from the push state, the `hoursTypeId`s Google's category offers from the
same read). So a change to the mapping changes the drift check with it.

**What is compared** — only the fields the push owns: a field in the patch's
update mask, valued or being cleared. A field Nexotec never pushed and holds
no value for is the dealer's own at Google and is never reported. Each field
is compared on what Nexotec sends, normalised the way Google answers:

- `address` — region, postal code, locality, address lines and (when we send
  it) the canton; case and surrounding blanks ignored;
- `phone` — the number, not its spelling: both sides read in the location's
  country plan (our `regionCode`, else Google's, else CH as the push assumes)
  and compared in E.164, so Google's national «044 123 45 67» is our
  `+41441234567` (KAN-794); a value that is no phone number at all falls
  back to its digits and a leading `+`;
- `website` — case and a trailing `/` ignored;
- `regularHours`, `moreHours` — the periods as sets, per `hoursTypeId` for
  more hours; a missing `TimeOfDay` part is 0 (Google's `{}` is 00:00);
- `specialHours` — the closed days of the full horizon, today until
  `closed_days_until(today)`: the nightly horizon push runs just before this
  check (KAN-793), so a day that entered the year since the last location
  change is at Google by now, and one that is not is drift. A day at Google
  with special opening hours (not closed) in the window is drift.

**Not compared:** photos (Nexotec never deletes a photo at Google, ADR-199,
and uploads none yet, KAN-784); a deactivated location — Nexotec pushes
nothing for it and never marks it closed (ADR-199), so it is `skipped`, not
drift; a binding that is not active (pending, in error, disconnected).

**Failures** are recorded, never raised: a refused grant moves the binding to
`error` (as the push does — the manager reconnects) and records
`grant_refused`; a transport failure, a timeout, a 5xx or an open breaker is
`provider_unavailable`; Google refusing the read is `provider_refused`. One
binding's failure never stops the others: each binding commits on its own.
Without Nexotec's client nothing is read (the client item on the status page
already says so, KAN-776).

**Staging runs no worker** (ADR-081): this job runs only where
`python -m app.worker` runs.
"""

import dataclasses
import datetime as dt
import logging
import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

import phonenumbers
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.base import utcnow
from app.core.relative_dates import today_in_session_zone
from app.integration.adapters.google_business_profile import GBP_CAPABILITY, GoogleBusinessProfileAdapter
from app.integration.errors import (
    ProviderAccountRefusedError,
    ProviderGatewayError,
    ProviderRequestError,
)
from app.integration.models.google_business_profile import (
    GbpBindingStatus,
    GbpDriftCheck,
    GbpDriftOutcome,
    GbpLocationBinding,
    GbpPushState,
)
from app.integration.services import gbp_link, gbp_push
from app.integration.services.gateway import call_capability
from app.integration.services.resilience import CircuitOpenError
from app.platform.public import LocationChannelFacts, closed_days_until, location_channel_facts

logger = logging.getLogger(__name__)

_PURPOSE = "business_profile_drift"

# Every Location field the push may write, plus the categories the mapping
# needs for more hours — one read per binding.
DRIFT_READ_MASK = (
    "storefrontAddress",
    "phoneNumbers",
    "websiteUri",
    "regularHours",
    "moreHours",
    "specialHours",
    "categories",
)

# The push's mask paths and the code the card and the status page name them by.
FIELD_CODES: Mapping[str, str] = {
    "storefrontAddress": "address",
    "phoneNumbers.primaryPhone": "phone",
    "websiteUri": "website",
    "regularHours": "regularHours",
    "moreHours": "moreHours",
    "specialHours": "specialHours",
}

# The reasons a check records (closed codes, never Google's text).
LOCATION_INACTIVE = "location_inactive"
LOCATION_NOT_FOUND = "location_not_found"
GRANT_REFUSED = "grant_refused"
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_REFUSED = "provider_refused"

_ADDRESS_KEYS = ("regionCode", "postalCode", "locality", "administrativeArea")


# -- the comparison (pure) ----------------------------------------------------------------


def _text(value: object) -> str:
    return " ".join(str(value).split()).casefold() if isinstance(value, str) else ""


def _address_view(address: object, *, keys: tuple[str, ...]) -> tuple:
    if not isinstance(address, dict):
        return ()
    lines = address.get("addressLines")
    return (
        tuple(_text(address.get(key)) for key in keys),
        tuple(_text(line) for line in lines) if isinstance(lines, list) else (),
    )


def _phone(value: object, *, region: str) -> str:
    if not isinstance(value, dict):
        return ""
    raw = str(value.get("primaryPhone") or "")
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return re.sub(r"[^\d+]", "", raw)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _region(expected: Mapping[str, Any], google: Mapping[str, Any]) -> str:
    """The country a national number is read in: the one the push sends,
    else the one Google holds, else CH (the push's own default)."""

    for address in (expected.get("storefrontAddress"), google.get("storefrontAddress")):
        if isinstance(address, dict) and isinstance(address.get("regionCode"), str) and address["regionCode"].strip():
            return address["regionCode"].strip().upper()
    return "CH"


def _website(value: object) -> str:
    return value.strip().rstrip("/").casefold() if isinstance(value, str) else ""


def _time(value: object) -> tuple[int, int]:
    if not isinstance(value, dict):
        return (0, 0)
    return (int(value.get("hours") or 0), int(value.get("minutes") or 0))


def _period_set(container: object) -> frozenset[tuple]:
    periods = container.get("periods") if isinstance(container, dict) else None
    if not isinstance(periods, list):
        return frozenset()
    return frozenset(
        (p.get("openDay"), _time(p.get("openTime")), p.get("closeDay"), _time(p.get("closeTime")))
        for p in periods
        if isinstance(p, dict)
    )


def _more_hours(value: object) -> dict[str, frozenset[tuple]]:
    if not isinstance(value, list):
        return {}
    out: dict[str, frozenset[tuple]] = {}
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("hoursTypeId"), str):
            out[item["hoursTypeId"]] = _period_set(item)
    return {type_id: periods for type_id, periods in out.items() if periods}


def _date(value: object) -> dt.date | None:
    if not isinstance(value, dict):
        return None
    try:
        return dt.date(int(value["year"]), int(value["month"]), int(value["day"]))
    except (KeyError, TypeError, ValueError):
        return None


def _special_days(value: object, *, start: dt.date, end: dt.date) -> frozenset[tuple[dt.date, bool]]:
    periods = value.get("specialHourPeriods") if isinstance(value, dict) else None
    if not isinstance(periods, list):
        return frozenset()
    out: set[tuple[dt.date, bool]] = set()
    for period in periods:
        if not isinstance(period, dict):
            continue
        day = _date(period.get("startDate"))
        if day is None or day < start or day > end:
            continue
        out.add((day, bool(period.get("closed"))))
    return frozenset(out)


def drifted_fields(
    patch: gbp_push.LocationPatch,
    google: Mapping[str, Any],
    *,
    today: dt.date,
) -> tuple[str, ...]:
    """The codes (`FIELD_CODES`) of the fields the push owns whose value at
    Google differs from what the push sends now. `google` is the Location
    body Google answered."""

    expected = patch.fields
    drifted: list[str] = []
    for path in patch.update_mask:
        code = FIELD_CODES[path]
        if path == "storefrontAddress":
            ours = expected.get("storefrontAddress") or {}
            keys = tuple(key for key in _ADDRESS_KEYS if key in ours)
            same = _address_view(ours, keys=keys) == _address_view(google.get("storefrontAddress"), keys=keys)
        elif path == "phoneNumbers.primaryPhone":
            region = _region(expected, google)
            same = _phone(expected.get("phoneNumbers"), region=region) == _phone(
                google.get("phoneNumbers"), region=region
            )
        elif path == "websiteUri":
            same = _website(expected.get("websiteUri")) == _website(google.get("websiteUri"))
        elif path == "regularHours":
            same = _period_set(expected.get("regularHours")) == _period_set(google.get("regularHours"))
        elif path == "moreHours":
            same = _more_hours(expected.get("moreHours")) == _more_hours(google.get("moreHours"))
        else:  # specialHours
            end = closed_days_until(today)
            same = _special_days(expected.get("specialHours"), start=today, end=end) == _special_days(
                google.get("specialHours"), start=today, end=end
            )
        if not same:
            drifted.append(code)
    return tuple(drifted)


# -- the job -----------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class DriftResult:
    outcome: GbpDriftOutcome
    drifted_fields: tuple[str, ...] = ()
    reason: str | None = None


@contextmanager
def _google(db: Session, connection) -> Iterator[GoogleBusinessProfileAdapter]:
    with call_capability(db, connection=connection, capability=GBP_CAPABILITY, purpose=_PURPOSE) as raw:
        yield cast(GoogleBusinessProfileAdapter, raw)


def _facts(db: Session, binding: GbpLocationBinding, today: dt.date) -> LocationChannelFacts | None:
    return location_channel_facts(db, dealership_id=binding.tenant_id, location_id=binding.location_id, today=today)


def check_binding(db: Session, binding: GbpLocationBinding, *, connection, today: dt.date) -> DriftResult:
    """Reads one binding's Google location and compares it. Never raises for
    a provider failure: the result says what happened."""

    facts = _facts(db, binding, today)
    if facts is None:
        return DriftResult(GbpDriftOutcome.SKIPPED, reason=LOCATION_NOT_FOUND)
    if not facts.is_active:
        return DriftResult(GbpDriftOutcome.SKIPPED, reason=LOCATION_INACTIVE)
    state = db.scalar(select(GbpPushState).where(GbpPushState.binding_id == binding.id))
    previously_pushed = frozenset(state.pushed_fields or []) if state is not None else frozenset()
    try:
        with _google(db, connection) as adapter:
            token = adapter.access_token(binding_id=binding.id)
            read = adapter.get_location(
                access_token=token,
                location_name=cast(str, binding.google_location_name),
                read_mask=DRIFT_READ_MASK,
            )
    except ProviderAccountRefusedError:
        gbp_link.mark_grant_refused(db, binding)
        return DriftResult(GbpDriftOutcome.FAILED, reason=GRANT_REFUSED)
    except ProviderRequestError:
        return DriftResult(GbpDriftOutcome.FAILED, reason=PROVIDER_REFUSED)
    except (ProviderGatewayError, CircuitOpenError):
        return DriftResult(GbpDriftOutcome.FAILED, reason=PROVIDER_UNAVAILABLE)
    patch = gbp_push.build_patch(
        facts,
        offered_hours_types=gbp_push.more_hours_types(read.body),
        previously_pushed=previously_pushed,
    )
    drifted = drifted_fields(patch, read.body, today=today)
    return DriftResult(GbpDriftOutcome.DRIFT if drifted else GbpDriftOutcome.IN_SYNC, drifted_fields=drifted)


def _record(db: Session, binding: GbpLocationBinding, result: DriftResult, *, now: dt.datetime) -> GbpDriftCheck:
    row = db.scalar(select(GbpDriftCheck).where(GbpDriftCheck.binding_id == binding.id))
    if row is None:
        row = GbpDriftCheck(binding_id=binding.id, tenant_id=binding.tenant_id)
        db.add(row)
    row.checked_at = now
    row.outcome = result.outcome
    row.drifted_fields = list(result.drifted_fields)
    row.reason = result.reason
    return row


def _active_bindings(db: Session) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(GbpLocationBinding.id)
            .where(
                GbpLocationBinding.status == GbpBindingStatus.ACTIVE,
                GbpLocationBinding.google_location_name.is_not(None),
            )
            .order_by(GbpLocationBinding.id)
        ).all()
    )


def run_drift_checks(db: Session, *, now: dt.datetime | None = None, today: dt.date | None = None) -> dict[str, int]:
    """Checks every active binding of every Betrieb once. Each binding
    commits on its own, so one failure never undoes another's result.
    Returns how many bindings had each outcome."""

    counts = {outcome.value: 0 for outcome in GbpDriftOutcome}
    connection = gbp_link.client_connection(db)
    if connection is None:
        logger.info("business profile drift: Nexotec's client is not set up; nothing read")
        return counts
    now = now or utcnow()
    today = today or today_in_session_zone()[0]
    for binding_id in _active_bindings(db):
        try:
            binding = db.get(GbpLocationBinding, binding_id, with_for_update={"skip_locked": True})
            if binding is None or binding.status != GbpBindingStatus.ACTIVE:
                db.rollback()
                continue
            result = check_binding(db, binding, connection=connection, today=today)
            _record(db, binding, result, now=now)
            db.commit()
            counts[result.outcome.value] += 1
        except Exception:
            db.rollback()
            logger.exception("business profile drift check failed", extra={"bindingId": str(binding_id)})
    return counts


def latest_checks(
    db: Session, *, dealership_id: uuid.UUID, binding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, GbpDriftCheck]:
    """The drift check of each listed binding of the Betrieb, by binding id."""

    if not binding_ids:
        return {}
    rows = db.scalars(
        select(GbpDriftCheck).where(GbpDriftCheck.tenant_id == dealership_id, GbpDriftCheck.binding_id.in_(binding_ids))
    ).all()
    return {row.binding_id: row for row in rows}


def unmapped_departments(
    db: Session, *, dealership_id: uuid.UUID, binding_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The departments the last push could not show at Google (no
    `hoursTypeId` offered for them), by binding id."""

    if not binding_ids:
        return {}
    rows = db.scalars(
        select(GbpPushState).where(GbpPushState.tenant_id == dealership_id, GbpPushState.binding_id.in_(binding_ids))
    ).all()
    return {row.binding_id: list(row.unmapped_departments or []) for row in rows}

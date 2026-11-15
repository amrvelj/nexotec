"""The Betrieb's status page («Alles grün»; KAN-223, Dealer Administration
D-A-38, FR-A-43).

One derived view per Betrieb: every item is computed from its source's own
data on each read — nothing is ever set by hand and health is never stored.
Green means every item is ok; anything else names the item and links to the
screen that fixes it.

Sources (Anto, 2026-11-14, Q1 A — the ones that exist; each other source joins
through `app.core.status` in its own ticket):

- **Number ranges** (platform's own): a range is a warning at 90 % of what its
  pattern's counter can hold and blocked at 100 % (no number left) — of the
  current year for a range that resets yearly.
- **Nexotec's status** (platform's own): every current
  `platform_status_incident`; one ok item when there is none.
- **Contributions** of contexts above platform (`app.core.status`, registered
  by `app/status_registry.py`): today integration's connections.

`derive` never writes. The managers are told by the daily job
(`status_notifications.py`), never by a read.
"""

import dataclasses
import datetime as dt
import logging
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.permissions import ModuleCode
from app.core.status import StatusItem, StatusState, status_providers, worst
from app.platform.models.status_page import PlatformStatusIncident
from app.platform.organisation.models.number_range import NumberRange
from app.platform.organisation.services import number_range as number_range_service
from app.platform.organisation.services.entitlement import list_active_modules

logger = logging.getLogger(__name__)

NUMBER_RANGE_SOURCE = "number_ranges"
NEXOTEC_SOURCE = "nexotec"

# A range is a warning from 90 % of its counter's capacity (Anto, 2026-11-14).
NUMBER_RANGE_WARNING_PERCENT = 90

_NUMBER_RANGES_LINK = "/administration/number-ranges"


@dataclasses.dataclass(frozen=True)
class StatusPage:
    dealership_id: uuid.UUID
    state: StatusState
    items: tuple[StatusItem, ...]
    # The current incidents behind the `nexotec.incident.*` items, by key —
    # the page shows their own four-language title and text.
    incidents: dict[str, PlatformStatusIncident]
    # Sources that raised on this read: their items are one
    # `<source>.unavailable` item, and their real items are unknown, not gone.
    unavailable_sources: frozenset[str] = frozenset()


def _number_range_item(row: NumberRange, *, year: int) -> StatusItem:
    parsed = number_range_service.parse_pattern(row.pattern)
    capacity = 10**parsed.counter_digits - 1
    restarts = row.yearly_reset and row.last_year != year
    used = 0 if row.last_value is None or restarts else row.last_value
    percent = min(100, used * 100 // capacity)
    if used >= capacity:
        state, message = StatusState.BLOCKED, "exhausted"
    elif percent >= NUMBER_RANGE_WARNING_PERCENT:
        state, message = StatusState.WARNING, "nearlyExhausted"
    else:
        state, message = StatusState.OK, "ok"
    return StatusItem(
        key=f"number_range.{row.document_type.value}",
        source=NUMBER_RANGE_SOURCE,
        state=state,
        message_key=f"statusPage.items.numberRange.{message}",
        link=_NUMBER_RANGES_LINK,
        params={"documentType": row.document_type.value, "percent": percent},
    )


def number_range_items(db: Session, dealership_id: uuid.UUID) -> list[StatusItem]:
    modules = [ModuleCode(code) for code in list_active_modules(db, dealership_id)]
    year = number_range_service.swiss_year()
    return [
        _number_range_item(row, year=year)
        for row in number_range_service.list_ranges(db, dealership_id=dealership_id, modules=modules)
    ]


def current_incidents(db: Session, now: dt.datetime) -> list[PlatformStatusIncident]:
    return list(
        db.scalars(
            select(PlatformStatusIncident)
            .where(
                PlatformStatusIncident.starts_at <= now,
                or_(PlatformStatusIncident.ends_at.is_(None), PlatformStatusIncident.ends_at > now),
            )
            .order_by(PlatformStatusIncident.starts_at, PlatformStatusIncident.id)
        ).all()
    )


def incident_key(incident: PlatformStatusIncident) -> str:
    return f"nexotec.incident.{incident.id}"


def nexotec_items(db: Session, now: dt.datetime) -> tuple[list[StatusItem], dict[str, PlatformStatusIncident]]:
    incidents = current_incidents(db, now)
    if not incidents:
        return [
            StatusItem(
                key="nexotec.platform",
                source=NEXOTEC_SOURCE,
                state=StatusState.OK,
                message_key="statusPage.items.nexotec.ok",
                link=None,
            )
        ], {}
    items = [
        StatusItem(
            key=incident_key(incident),
            source=NEXOTEC_SOURCE,
            state=StatusState(incident.severity.value),
            message_key="statusPage.items.nexotec.incident",
            link=None,
        )
        for incident in incidents
    ]
    return items, {incident_key(incident): incident for incident in incidents}


def derive(db: Session, dealership_id: uuid.UUID, *, now: dt.datetime) -> StatusPage:
    """The Betrieb's page. A contribution that fails is logged and shown as
    one blocked item of its source — the page never answers «green» for a
    source it could not ask."""

    items: list[StatusItem] = [*number_range_items(db, dealership_id)]
    nexotec, incidents = nexotec_items(db, now)
    unavailable: set[str] = set()
    for provider in status_providers():
        try:
            # A savepoint, so a source whose query failed leaves the caller's
            # transaction usable for the sources after it.
            with db.begin_nested():
                items.extend(provider.items(db, dealership_id=dealership_id, now=now))
        except Exception:
            logger.exception(
                "status page: source %s failed for Betrieb %s",
                provider.source,
                dealership_id,
                extra={"dealershipId": str(dealership_id), "source": provider.source},
            )
            unavailable.add(provider.source)
            items.append(
                StatusItem(
                    key=f"{provider.source}.unavailable",
                    source=provider.source,
                    state=StatusState.BLOCKED,
                    message_key="statusPage.items.sourceUnavailable",
                    link=None,
                )
            )
    items.extend(nexotec)
    return StatusPage(
        dealership_id=dealership_id,
        state=worst([item.state for item in items]),
        items=tuple(items),
        incidents=incidents,
        unavailable_sources=frozenset(unavailable),
    )

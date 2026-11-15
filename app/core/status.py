"""The status page's contribution port (KAN-223; Dealer Administration
D-A-38, FR-A-43).

The status page («Alles grün») is platform's (`app/platform/services/
status_page.py`): one derived screen per Betrieb that names every item that
is not green and links to the screen that fixes it. Platform derives its own
items (number ranges, Nexotec's own status), but some sources are owned by a
context above platform — the integration connections are the first, sending
identities and Google Business Profile drift follow in their own tickets.
Platform is the lowest layer and never calls up (ADR-131), so such a source
arrives here: the owning context implements `StatusProvider` inside its own
package and a composition root directly under `app/` (`app/status_registry.py`,
imported by `app.main` and `app.worker`) registers it at start-up, as
`app/ownership_registry.py` registers the ownership providers. Platform reads
the registered providers through `status_providers()`.

**Derived, never stored.** A provider computes its items from its own data on
the caller's session on every read and must not write; the status page never
computes a source's health itself (the ticket's «Do not touch»). The only
stored part of the status page is platform's notification dedup state
(`status_item_state`), which is about who was told, not about health.

Core imports no bounded context: an item is plain data, a provider a protocol.
"""

import dataclasses
import datetime as dt
import enum
import uuid
from typing import Protocol

from sqlalchemy.orm import Session


class StatusState(str, enum.Enum):
    OK = "ok"
    WARNING = "warning"
    BLOCKED = "blocked"


# The order the page and the overall state use: the worst item wins.
_SEVERITY = {StatusState.OK: 0, StatusState.WARNING: 1, StatusState.BLOCKED: 2}


def worst(states: "list[StatusState] | tuple[StatusState, ...]") -> StatusState:
    """The overall state of a set of items: blocked over warning over ok; an
    empty set is ok."""

    result = StatusState.OK
    for state in states:
        if _SEVERITY[state] > _SEVERITY[result]:
            result = state
    return result


@dataclasses.dataclass(frozen=True)
class StatusItem:
    # Stable per Betrieb across reads — the dedup state is keyed by it
    # (`integration.connection.<id>`, `number_range.offer`). At most 96
    # characters; unique across every source.
    key: str
    # The source that contributed it (`integration`, `number_ranges`,
    # `nexotec`): the page groups by it.
    source: str
    state: StatusState
    # An SPA i18n key (`statusPage.items.integration.expired`); never English
    # text from the API.
    message_key: str
    # The SPA path that fixes it (`/administration/integrations`), or None
    # when nothing on the dealer side can (Nexotec's own incidents).
    link: str | None
    # Values the message interpolates (a provider's name, a percentage).
    params: dict[str, str | int] = dataclasses.field(default_factory=dict)
    # True only when the source has really announced this state to the
    # managers itself (an integration whose T-30/14/7 expiry notice was sent,
    # KAN-222), so the status job does not tell them a second time. Honoured
    # for a warning only: the move to blocked is always announced.
    notified_by_source: bool = False


class StatusProvider(Protocol):
    # The source name its items carry (`integration`).
    source: str

    def items(self, db: Session, *, dealership_id: uuid.UUID, now: dt.datetime) -> list[StatusItem]:
        """Every item this source has for the Betrieb — ok ones included, so
        the page can say what it checks. A read on the caller's session; must
        not write."""
        ...


_providers: dict[str, StatusProvider] = {}


def register_status_provider(provider: StatusProvider) -> None:
    """Registers a source another context owns. Registering the same source
    twice replaces it (a reload in tests); two contexts may not share one."""

    _providers[provider.source] = provider


def unregister_status_provider(source: str) -> None:
    _providers.pop(source, None)


def status_providers() -> tuple[StatusProvider, ...]:
    return tuple(_providers[source] for source in sorted(_providers))

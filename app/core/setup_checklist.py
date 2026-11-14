"""The setup checklist's contribution port (KAN-214; Dealer Administration
D-A-21, FR-A-26).

The checklist is platform's (`app/platform/services/setup_checklist.py`), but
some of its items are owned by a context above platform — the publishing
profile (inventory, KAN-225) is the first. Platform is the lowest layer and
never calls up (ADR-131), so such an item arrives here: the owning context
describes it as a `ChecklistContribution`, and a composition root directly
under `app/` registers it at start-up, as `app/ownership_registry.py`
registers the ownership providers (that root is added with the first
contribution — today there is nothing to register). Platform reads the
registered contributions through `checklist_contributions()` and derives them
like its own items — never stored, never ticked.

None is registered today: the publishing profile does not exist yet, and its
item joins the checklist when it ships (Anto, 2026-11-14, KAN-214 Q5 A). Until
then a Betrieb holding `sales` activates without it; once it is registered, an
active Betrieb whose profile is missing is refused only the action the item
blocks (marketplace publishing), like any regression after activation.

Core imports no bounded context: a contribution is plain data plus a callback
on the caller's session.
"""

import dataclasses
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session


@dataclasses.dataclass(frozen=True)
class ChecklistContribution:
    # A stable item code, unique across the checklist (`publishing_profile`).
    code: str
    # The modules (entitlement codes) the item applies to; empty = every Betrieb.
    applies_to_modules: frozenset[str]
    # The action an incomplete item refuses after activation — one of
    # platform's `BlockedAction` values (`marketplace_publishing`), or None
    # when the item blocks activation only.
    blocks: str | None
    # The SPA path that fixes it (`/administration/publishing`).
    link: str
    # Whether the item is complete for the Betrieb, read from the owner's data
    # on the caller's session. Must not write.
    is_complete: Callable[[Session, uuid.UUID], bool]


_contributions: dict[str, ChecklistContribution] = {}


def register_checklist_contribution(contribution: ChecklistContribution) -> None:
    """Registers an item another context owns. Registering the same code twice
    replaces it (a reload in tests); two contexts may not share a code."""

    _contributions[contribution.code] = contribution


def unregister_checklist_contribution(code: str) -> None:
    _contributions.pop(code, None)


def checklist_contributions() -> tuple[ChecklistContribution, ...]:
    return tuple(_contributions[code] for code in sorted(_contributions))

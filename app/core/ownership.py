"""The ownership-provider seam (KAN-200; Dealer Administration FR-A-37,
D-A-35): "each module registers an ownership provider — what does this user
own that is still open?" — so that deactivating a user lists those records
grouped by module and lets the manager transfer each group to a colleague
or leave it unassigned, never silently with the leaver.

Cross-cutting on purpose (CLAUDE.md rule 8): platform asks this registry,
never a context. Each context implements the protocol inside its own
package and registers the instance from the composition root
(`app/ownership_registry.py`, imported by `app.main` and `app.worker`);
platform imports nothing from any context and no context imports platform's
deactivation.

ADR-047 (CLAUDE.md rule 12): a transfer is a write inside the owning
context. It runs on a session of its own — the caller hands in a session
FACTORY, never its request session — and commits there, so a failed
transfer in one module never rolls back platform's status change or another
module's transfer. The platform side records what happened per group
(audit row with the actor) and, when a module fails, leaves the records with
the user for the retry endpoint, which reads `list_open` again: there is no
shared transaction to undo, so the compensating action is "run the transfer
again".

A provider that owns nothing per user (stock belongs to the Betrieb, not to
a person) still registers and answers an empty list: the registry is the
complete inventory of modules, which is what makes "nothing was forgotten"
a statement the deactivation can make.
"""

import dataclasses
import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session


@dataclasses.dataclass(frozen=True)
class OwnedGroup:
    """One group of open records a user owns, as the deactivation dialog
    lists it: the module (`sales`, `customer`, …), the group inside it
    (`offers`, `contracts`, `customers`, …) and how many. Labels are the
    SPA's (i18n by `module` + `group`), never English here."""

    module: str
    group: str
    count: int


@dataclasses.dataclass(frozen=True)
class OwnershipTransfer:
    """What the manager decided for one group: a colleague, or nobody
    (`to_user_id` None = the module's own unassigned queue). `to_label` is
    the display label the receiving records denormalise (rule 2's
    three-column pattern); platform resolves it from the user row once."""

    module: str
    group: str
    to_user_id: uuid.UUID | None
    to_label: str | None


@dataclasses.dataclass(frozen=True)
class OwnershipRefusal:
    """Why a module will not hand a group to the chosen colleague: a stable
    `code` the SPA translates (the module's own vocabulary, e.g. Sales'
    `colleague_cannot_act_there`), the English `message` for the API body,
    and how many records the refusal is about."""

    code: str
    message: str
    count: int


class OwnershipProvider(Protocol):
    module: str

    def list_open(self, db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> list[OwnedGroup]:
        """The groups of open records this user owns — only groups with at
        least one record. A read on the caller's session. A user id names
        one person platform-wide, so a provider counts what the user owns
        anywhere (an offer a sister-Betrieb member created there is theirs
        too); `dealership_id` is the user's home, for a provider whose
        records are per Betrieb and whose colleague check needs it."""
        ...

    def refuses(self, db: Session, *, from_user_id: uuid.UUID, transfer: OwnershipTransfer) -> OwnershipRefusal | None:
        """Why this module would refuse the colleague for this group, or None. A read on the caller's session, asked
        before the deactivation changes anything, so a wrong choice is a 422
        with the user still active; `transfer` checks the same thing again
        under its locks (the records may have moved meanwhile)."""
        ...

    def transfer(
        self,
        session_factory: Callable[[], Session],
        *,
        dealership_id: uuid.UUID,
        from_user_id: uuid.UUID,
        transfer: OwnershipTransfer,
        actor_id: uuid.UUID | None,
        reason: str | None,
    ) -> int:
        """Moves every open record of `transfer.group` from the user to the
        colleague (or to nobody), on a session of its own that this method
        commits (ADR-047). Returns how many records moved. An unknown group
        for this module is a `ValueError`. A colleague who cannot act in a
        Betrieb some of the records belong to is refused as a whole — a 422
        saying how many records are elsewhere, never which Betrieb, nothing
        moved — so the manager chooses the queue for that group instead. `reason` is the manager's text (or the job's code)
        for the module's own audit rows."""
        ...


_REGISTRY: dict[str, OwnershipProvider] = {}


def register_ownership_provider(provider: OwnershipProvider) -> None:
    """Idempotent by module: registering a second instance for the same
    module replaces the first (the composition root may be imported more
    than once in one process — tests do)."""

    _REGISTRY[provider.module] = provider


def ownership_providers() -> list[OwnershipProvider]:
    return [_REGISTRY[module] for module in sorted(_REGISTRY)]


def ownership_provider(module: str) -> OwnershipProvider | None:
    return _REGISTRY.get(module)


def registered_ownership_modules() -> list[str]:
    return sorted(_REGISTRY)

"""The seam through which a context above platform joins «copy configuration
from a sister Betrieb» (KAN-220, FR-A-01; KAN-818 is the first: Aftersales'
service catalogue).

Platform runs the copy — who may copy, from where, the one transaction, the
dry run's savepoint, the summary audit row — and imports no context above it
(calls go only down, `test_context_dependency_direction.py`). A context that
owns configuration of its own registers a `ConfigurationCopier` here from the
composition root (`app/configuration_copy_registry.py`); platform asks this
registry for the subset the manager ticked. Written as if it were already an
external package: no import of any bounded context.

A copier writes through its own entity's write path (validation, audit row,
version) **on a session of its own** that it commits — or, for the dry run,
rolls back after the very same writes — never inside platform's transaction:
a write spanning two contexts is never a shared transaction (rule 12,
ADR-047). Platform runs the registered copiers before its own subsets; each
side commits its own work, and a copy is safe to run again (what the target
already holds as the source has it is `unchanged`), so a failure of the other
side needs no compensation. It reports one `CopiedItem` per thing it looked
at, keyed in the target's terms, never by an id of the source's row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

# The outcomes platform's report knows (its CopyOutcome values).
CREATED = "created"
OVERWRITTEN = "overwritten"
UNCHANGED = "unchanged"
SKIPPED = "skipped"


@dataclass(frozen=True)
class CopiedItem:
    key: str
    outcome: str
    label: str | None = None
    # Why an item was skipped: a stable reason code.
    reason: str | None = None


class ConfigurationCopier(Protocol):
    # The subset's code, as the copy request names it.
    subset: str
    # The module both Betriebe must hold (a ModuleCode value), or None.
    module: str | None

    def copy(
        self,
        session_factory: Callable[[], Session],
        *,
        source_dealership_id: uuid.UUID,
        target_dealership_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        dry_run: bool,
    ) -> list[CopiedItem]:
        """Opens its own session, writes, and commits it — or rolls it back
        when `dry_run` — before it returns."""
        ...


_REGISTRY: dict[str, ConfigurationCopier] = {}


def register_configuration_copier(copier: ConfigurationCopier) -> None:
    """Idempotent by subset: a second registration replaces the first (the
    composition root may be imported more than once in one process)."""

    _REGISTRY[copier.subset] = copier


def configuration_copier(subset: str) -> ConfigurationCopier | None:
    return _REGISTRY.get(subset)

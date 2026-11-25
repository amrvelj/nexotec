"""Restate `platform.module_entitlement.granted` for every business that holds a
module now (KAN-808 PR-1d; ADR-222 (4)).

Platform states the fact when a module becomes active (`set_entitlements`,
`app/platform/organisation/services/entitlement_facts.py`). A business that held
the module before the fact existed never got one, so what a context above
platform keeps per module never arrived — for `aftersales`, the planner's
starting sets. This script states the fact again for each such business, with
the same payload shape; every consumer of it is harmless on a repeat (rule 6;
for the starting sets, planning's marker row copies a business's sets once,
whatever arrives later). It writes platform's outbox only: no other context.

An offboarded business is left out (it uses nothing any more), and so is the
platform tenant (it holds no entitlements). One correlation id spans the run,
so every event it states is traceable to it.

MANDATORY DRY RUN. The default writes nothing: the events are added in one
transaction that is rolled back, and the report lists each business a real run
would restate the fact for. Re-run with --commit after reading it, the
convention of the data-migration scripts (`.claude/rules/migrations-scripts.md`).

Re-runnable: a second --commit run states the fact again — harmless by the
consumers' own rule, so the run is safe to repeat, but a repeat has no use.

Exit status: 0 always when it ran (the report is the result); 2 when the module
is not one Nexotec licenses.

Usage:
    DMS_DATABASE_URL=... python scripts/restate_module_entitlements.py --module aftersales           # dry run
    DMS_DATABASE_URL=... python scripts/restate_module_entitlements.py --module aftersales --commit  # writes
"""

import argparse
import sys
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.correlation import correlation_scope
from app.core.permissions import ModuleCode
from app.db import SessionLocal
from app.platform.organisation.services.entitlement_facts import (
    MODULE_ENTITLEMENT_GRANTED_EVENT,
    RestatedGrant,
    restate_module_granted,
)


def run(db: Session, *, module: ModuleCode, commit: bool) -> tuple[uuid.UUID, list[RestatedGrant]]:
    """States the fact for every Betrieb holding `module`; commits, or rolls
    the very same writes back for the dry run. Returns the run's correlation id
    and what was (or would be) stated."""

    with correlation_scope() as correlation_id:
        restated = restate_module_granted(db, module=module)
    if commit:
        db.commit()
    else:
        db.rollback()
    return correlation_id, restated


def render(module: ModuleCode, correlation_id: uuid.UUID, restated: list[RestatedGrant], *, committed: bool) -> str:
    mode = "COMMITTED" if committed else "DRY RUN — nothing written"
    lines = [
        f"KAN-808 restate {MODULE_ENTITLEMENT_GRANTED_EVENT} for module {module.value} ({mode})",
        f"correlation id: {correlation_id}",
        "",
        f"businesses: {len(restated)}",
    ]
    for grant in restated:
        lines.append(f"  {grant.dealership_id} ({grant.legal_name}) — entitlement {grant.entitlement_id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, session_factory: Callable[[], Session] = SessionLocal) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", required=True, help="the module code, e.g. aftersales")
    parser.add_argument(
        "--commit", action="store_true", help="Actually write. Omit for a dry run (default) — read the report first."
    )
    args = parser.parse_args(argv)
    try:
        module = ModuleCode(args.module)
    except ValueError:
        print(f"KAN-808: {args.module!r} is not a module ({', '.join(m.value for m in ModuleCode)})")
        return 2

    db = session_factory()
    try:
        correlation_id, restated = run(db, module=module, commit=args.commit)
    finally:
        db.close()

    print(render(module, correlation_id, restated, committed=args.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())

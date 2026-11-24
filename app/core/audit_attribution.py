"""KAN-722: which Betrieb wrote an audit row of a group-owned record.

A group-owned contact's audit rows carry the group as `tenant_id` (ADR-014),
so the row's tenant does not say which Betrieb of the group wrote it. The
one rule, used by both readers that need it — the History tab of a group
contact (hides an offboarded Betrieb's rows from its sisters) and the core
exporter (puts exactly those rows in that Betrieb's archive) — so the hidden
set and the exported set cannot drift apart (Anto, 2026-11-15):

- a row with `acting_dealership_id` (written since KAN-722) is its Betrieb's;
- an older row without it is a Betrieb's when its author's only Betrieb in
  the group is that one (Anto, 2026-11-15, answer A). A row by someone who
  also worked in a sister, or by no one (a consumer, a system job), is no
  single Betrieb's and is attributed to none.

Platform owns the organisation, so it computes the authors
(`platform.public.get_audit_attribution`); this module only turns the answer
into a predicate. Cross-cutting: it imports no bounded context."""

import dataclasses
import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_


@dataclasses.dataclass(frozen=True)
class AuditAttribution:
    """The Betriebe whose rows are meant, and the authors whose rows without
    an acting Betrieb are theirs: each one's only Betrieb in the group is
    one of `dealership_ids`."""

    dealership_ids: frozenset[uuid.UUID] = frozenset()
    sole_author_ids: frozenset[uuid.UUID] = frozenset()

    def is_empty(self) -> bool:
        return not self.dealership_ids


NOBODY = AuditAttribution()


def attributed(columns: Any, attribution: AuditAttribution) -> ColumnElement[bool]:
    """The rows of `attribution`'s Betriebe. `columns` is anything with the
    audit columns as attributes: the `AuditEvent` class or its table's `.c`.

    Never NULL for any row (each arm guards its nullable column), so
    `not_(attributed(...))` keeps exactly the other rows — the History read
    relies on it."""

    if attribution.is_empty():
        return false()
    acting, actor = columns.acting_dealership_id, columns.actor_id
    by_acting = and_(acting.is_not(None), acting.in_(sorted(attribution.dealership_ids)))
    if not attribution.sole_author_ids:
        return by_acting
    by_author = and_(acting.is_(None), actor.is_not(None), actor.in_(sorted(attribution.sole_author_ids)))
    return or_(by_acting, by_author)

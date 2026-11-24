"""KAN-722 (Anto, 2026-11-15): which Betrieb of a dealer group wrote an audit
row of a group-owned record, as `app.core.audit_attribution` needs it — the
Betriebe meant and the authors whose rows without an acting Betrieb are
theirs. Two readers use it, so what a sister stops seeing is what the
leaver's archive holds: the History tab of a group contact (the offboarded
Betriebe of the reader's group) and platform's export scope (the exported
Betrieb).

Its own module so the ambient-group-read allowlist
(`tests/architecture/test_no_ambient_group_read.py`) names this one read and
nothing beside it: it lists the Betriebe of a group and the users who work
in them, platform's own tables only, and hands back ids, never a row."""

import uuid

from sqlalchemy import case, func, literal_column, select, union_all
from sqlalchemy.orm import Session

from app.core.audit_attribution import NOBODY, AuditAttribution
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.user import User
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.services.dealer_dealerships import dealer_dealerships_only


def get_audit_attribution(
    db: Session, *, group_id: uuid.UUID, dealership_ids: frozenset[uuid.UUID]
) -> AuditAttribution:
    """`dealership_ids` narrowed to the Betriebe of `group_id`, and every user
    whose only Betrieb in that group is one of them: their home, or a
    membership — ended ones included (a membership's row stays, KAN-199), for
    a person who once worked in a sister is no single Betrieb's author
    (Anto, 2026-11-15, answer A)."""

    group_betriebe = select(Dealership.id).where(Dealership.dealer_group_id == group_id, dealer_dealerships_only())
    meant = frozenset(db.scalars(group_betriebe.where(Dealership.id.in_(sorted(dealership_ids)))).all())
    if not meant:
        return NOBODY
    works_in = union_all(
        select(User.id.label("user_id"), User.tenant_id.label("dealership_id")).where(
            User.tenant_id.in_(group_betriebe)
        ),
        select(DealershipMembership.user_id, DealershipMembership.dealership_id).where(
            DealershipMembership.dealership_id.in_(group_betriebe)
        ),
    ).subquery()
    in_meant = case((works_in.c.dealership_id.in_(sorted(meant)), 1), else_=0)
    authors = db.scalars(
        select(works_in.c.user_id)
        .group_by(works_in.c.user_id)
        .having(func.count(works_in.c.dealership_id.distinct()) == 1, func.sum(in_meant) > literal_column("0"))
    ).all()
    return AuditAttribution(dealership_ids=meant, sole_author_ids=frozenset(authors))


def get_offboarded_audit_attribution(db: Session, *, group_id: uuid.UUID) -> AuditAttribution:
    """`get_audit_attribution` for every offboarded Betrieb of `group_id` —
    the rows the History tab of a group contact no longer shows (KAN-722;
    KAN-218 Q2 A: an offboarded Betrieb leaves every group read)."""

    offboarded = frozenset(
        db.scalars(
            select(Dealership.id).where(
                Dealership.dealer_group_id == group_id,
                Dealership.status == DealershipStatus.OFFBOARDED,
                dealer_dealerships_only(),
            )
        ).all()
    )
    if not offboarded:
        return NOBODY
    return get_audit_attribution(db, group_id=group_id, dealership_ids=offboarded)

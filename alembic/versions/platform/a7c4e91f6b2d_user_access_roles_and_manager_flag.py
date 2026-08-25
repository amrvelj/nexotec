"""user.access_roles + is_dealer_manager (WP-2 PR-2, closes G-08 / RP-1 leak)

Replaces the scalar `access_role` column — "runs the dealership" and "may
use this module" were the same field by accident, not by decision.

Backfill (Roles & Permissions "Migration from today"):
  - access_role = dealer_admin  -> is_dealer_manager = true, plus a
    functional role inferred from the user's job title (`role`), defaulting
    to sales for any title with no clean mapping (gm, admin, other).
  - access_role = sales/inventory/auditor/platform_admin -> unchanged,
    wrapped in a one-element set.

This is a genuine data migration — the job-title inference is per-row
conditional logic, not expressible as a single static UPDATE — so unlike
most migrations in this repo it cannot be reviewed via `alembic ... --sql`;
it only runs online, against a real connection. Review the ROLE_FROM_TITLE
table below directly instead.

A dealership must always have at least one active manager (enforcement
rule 7) — including immediately after this migration. Every dealership
with zero dealer_admin users going in is a data-integrity gap that already
existed before this migration touched anything; this migration surfaces it
rather than papering over it. It promotes that dealership's oldest active
(status IN invited/active) user to manager and prints one line per
dealership so affected, to REVIEW_MIGRATION_REPORT — grep the migration
output for that marker after running this in staging/production.

Revision ID: a7c4e91f6b2d
Revises: f2a6c1b7de3c
Create Date: 2026-08-24 00:00:00.000000

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c4e91f6b2d'
down_revision: Union[str, Sequence[str], None] = 'f2a6c1b7de3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# UserRole (job title) -> functional AccessRole, applied only to rows whose
# OLD access_role was 'DEALER_ADMIN' — every other role passes through
# unchanged. See app/core/auth.py::AccessRole for the target enum and the
# PR description for why each of these was chosen; gm/admin/other have no
# clean mapping and fall to the stated default.
ROLE_FROM_TITLE = {
    "SALES": "sales",
    "SERVICE_ADVISOR": "aftersales",
    "FINANCE_MANAGER": "finance",
    "PARTS": "parts",
    "TECHNICIAN": "aftersales",
    "GM": "sales",
    "ADMIN": "sales",
    "OTHER": "sales",
}
_ACTIVE_USER_STATUSES = ("INVITED", "ACTIVE")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("user", sa.Column("access_roles", sa.JSON(), nullable=True))
    op.add_column(
        "user", sa.Column("is_dealer_manager", sa.Boolean(), nullable=False, server_default=sa.false())
    )

    user = sa.table(
        "user",
        sa.column("id", sa.String),
        sa.column("tenant_id", sa.String),
        sa.column("role", sa.String),
        sa.column("access_role", sa.String),
        sa.column("access_roles", sa.JSON),
        sa.column("is_dealer_manager", sa.Boolean),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime),
    )

    rows = bind.execute(
        sa.select(user.c.id, user.c.tenant_id, user.c.role, user.c.access_role, user.c.status)
    ).all()

    for row in rows:
        if row.access_role == "DEALER_ADMIN":
            roles = [ROLE_FROM_TITLE.get(row.role, "sales")]
            is_manager = True
        else:
            roles = [row.access_role.lower()]
            is_manager = False
        bind.execute(
            user.update()
            .where(user.c.id == row.id)
            .values(access_roles=roles, is_dealer_manager=is_manager)
        )

    # Enforcement rule 7: at least one active manager per dealership, even
    # right after this migration. A dealership with zero dealer_admin rows
    # going in would otherwise come out the other side with zero managers.
    tenants_missing_a_manager = bind.execute(
        sa.select(sa.distinct(user.c.tenant_id)).where(
            user.c.tenant_id.not_in(
                sa.select(user.c.tenant_id).where(
                    user.c.is_dealer_manager.is_(True), user.c.status.in_(_ACTIVE_USER_STATUSES)
                )
            )
        )
    ).scalars().all()

    for tenant_id in tenants_missing_a_manager:
        oldest_active_user_id = bind.execute(
            sa.select(user.c.id)
            .where(user.c.tenant_id == tenant_id, user.c.status.in_(_ACTIVE_USER_STATUSES))
            .order_by(user.c.created_at)
            .limit(1)
        ).scalar()
        if oldest_active_user_id is None:
            # No active user at all in this tenant — nothing to promote;
            # still reported below so it isn't silently invisible.
            print(f"REVIEW_MIGRATION_REPORT: tenant {tenant_id} has NO active user to promote to manager.")
            continue
        bind.execute(
            user.update().where(user.c.id == oldest_active_user_id).values(is_dealer_manager=True)
        )
        print(
            f"REVIEW_MIGRATION_REPORT: tenant {tenant_id} had no manager — "
            f"promoted its oldest active user ({oldest_active_user_id}) to is_dealer_manager=true."
        )

    op.alter_column("user", "access_roles", nullable=False)
    op.drop_column("user", "access_role")


def downgrade() -> None:
    bind = op.get_bind()
    op.add_column("user", sa.Column("access_role", sa.String(length=32), nullable=True))

    user = sa.table(
        "user",
        sa.column("id", sa.String),
        sa.column("access_roles", sa.JSON),
        sa.column("is_dealer_manager", sa.Boolean),
        sa.column("access_role", sa.String),
    )
    rows = bind.execute(sa.select(user.c.id, user.c.access_roles, user.c.is_dealer_manager)).all()
    for row in rows:
        roles = json.loads(row.access_roles) if isinstance(row.access_roles, str) else row.access_roles
        # Lossy by construction: a manager's OTHER functional roles (and
        # any user holding more than one role) don't survive a downgrade —
        # there is no scalar value that represents a set. is_dealer_manager
        # wins over whatever else is in the set, mirroring how the set was
        # built from it in the first place.
        access_role = "DEALER_ADMIN" if row.is_dealer_manager else (roles[0].upper() if roles else "SALES")
        bind.execute(user.update().where(user.c.id == row.id).values(access_role=access_role))

    op.alter_column("user", "access_role", nullable=False)
    op.drop_column("user", "is_dealer_manager")
    op.drop_column("user", "access_roles")

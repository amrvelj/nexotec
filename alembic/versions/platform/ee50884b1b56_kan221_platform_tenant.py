"""KAN-221 PR-1: the platform tenant — dealership.kind (dealer | platform) and the one platform Betrieb «Nexotec» in its own group

Revision ID: ee50884b1b56
Revises: b8d2f4a61c39
Create Date: 2026-11-18 14:00:00.000000

The platform Betrieb homes Nexotec's staff and nothing else: no location, no
settings row, no number ranges, no entitlements, no setup checklist. It is
created `active` with `activated_at` NULL, so the session guard lets its
staff in and the checklist-regression job never looks at it. Its seat has no
address of its own (the columns are NOT NULL, so they hold empty strings):
it is never printed, exported or served — every dealer read leaves it out
(`app/platform/organisation/services/dealer_dealerships.py`).

The literals below are frozen here (ADR-133) and equal to
`app.platform.organisation.models.dealership`'s PLATFORM_* constants
(`tests/platform/test_kan221_platform_tenant.py`).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = 'ee50884b1b56'
down_revision: str | Sequence[str] | None = 'b8d2f4a61c39'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision.
PLATFORM_DEALER_GROUP_ID = "01a1eefe-1396-7ae0-a91b-431928446395"
PLATFORM_DEALERSHIP_ID = "01a1eefe-1396-7ae1-83af-8d53b7546b3c"
PLATFORM_TENANT_NAME = "Nexotec"
# enum_check_condition's wording, in enum order.
_KIND_CHECK = "\"kind\" IN ('dealer', 'platform')"


def upgrade() -> None:
    op.add_column(
        'dealership',
        sa.Column('kind', sa.String(length=16), server_default='dealer', nullable=False),
    )
    op.create_check_constraint('ck_dealership_kind_enum', 'dealership', _KIND_CHECK)
    op.create_index(
        'uq_dealership_one_platform',
        'dealership',
        ['kind'],
        unique=True,
        postgresql_where=sa.text("kind = 'platform'"),
        sqlite_where=sa.text("kind = 'platform'"),
    )
    if context.is_offline_mode():
        op.execute("-- KAN-221 platform tenant rows skipped: the insert checks existing rows and needs a live database")
        return
    bind = op.get_bind()
    params = {"group_id": PLATFORM_DEALER_GROUP_ID, "dealership_id": PLATFORM_DEALERSHIP_ID, "name": PLATFORM_TENANT_NAME}
    bind.execute(
        sa.text(
            "INSERT INTO dealer_group (id, name, group_read_enabled, version, created_at, updated_at) "
            "SELECT :group_id, :name, false, 1, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM dealer_group WHERE id = :group_id)"
        ),
        params,
    )
    bind.execute(
        sa.text(
            "INSERT INTO dealership (id, dealer_group_id, kind, legal_name, franchise_type, "
            "address_street, address_house_number, address_postal_code, address_locality, address_canton, "
            "address_country, phone, vat_liable, status, brand_primary_color, default_correspondence_language, "
            "entitlements_version, version, created_at, updated_at) "
            "SELECT :dealership_id, :group_id, 'platform', :name, 'independent', "
            "'', '', '', '', '', 'CH', '', false, 'active', '#7C3AED', 'de', 0, 1, now(), now() "
            "WHERE NOT EXISTS (SELECT 1 FROM dealership WHERE kind = 'platform')"
        ),
        params,
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        bind = op.get_bind()
        homed = bind.execute(
            sa.text('SELECT count(*) FROM "user" WHERE tenant_id = :id'), {"id": PLATFORM_DEALERSHIP_ID}
        ).scalar_one()
        if homed:
            raise RuntimeError(
                f"KAN-221: {homed} user(s) are homed in the platform tenant — move them before downgrading; "
                "this downgrade deletes no user."
            )
        bind.execute(sa.text("DELETE FROM dealership WHERE id = :id"), {"id": PLATFORM_DEALERSHIP_ID})
        bind.execute(
            sa.text(
                "DELETE FROM dealer_group WHERE id = :id "
                "AND NOT EXISTS (SELECT 1 FROM dealership WHERE dealer_group_id = :id)"
            ),
            {"id": PLATFORM_DEALER_GROUP_ID},
        )
    op.drop_index(
        'uq_dealership_one_platform',
        table_name='dealership',
        postgresql_where=sa.text("kind = 'platform'"),
        sqlite_where=sa.text("kind = 'platform'"),
    )
    op.drop_constraint('ck_dealership_kind_enum', 'dealership', type_='check')
    op.drop_column('dealership', 'kind')

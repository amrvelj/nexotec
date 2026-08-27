"""user.auth_identity_id unique constraint (WP-4, ADR-016)

auth_identity_id has existed since the very first User migration as a
placeholder FK to an external subject, but with no uniqueness constraint —
it was just an arbitrary client-supplied string. WP-4 makes it real: the
Zitadel `sub` claim, looked up at login (app.platform.api.auth::
oidc_callback) to map an authenticated external identity to exactly one
internal User. Without this constraint two User rows could claim the same
Zitadel subject, and login would resolve to whichever one the database
happened to return first — a real cross-tenant identity confusion risk,
not a cosmetic gap.

If a real deployed database already has duplicate auth_identity_id values,
this migration fails loudly on the ADD CONSTRAINT statement — correct
behavior, no bespoke pre-check needed; a duplicate here means two accounts
would already be able to impersonate each other in the new login flow, and
that has to be resolved by hand before this can land, not silently patched
by a migration.

Revision ID: b8e4c17a9d5f
Revises: a2f8c5e91b3d
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8e4c17a9d5f'
down_revision: Union[str, Sequence[str], None] = 'a2f8c5e91b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("uq_user_auth_identity_id", "user", ["auth_identity_id"])


def downgrade() -> None:
    op.drop_constraint("uq_user_auth_identity_id", "user", type_="unique")

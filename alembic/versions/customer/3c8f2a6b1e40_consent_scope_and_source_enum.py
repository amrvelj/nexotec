"""FR-23 §1 — per-channel consent scope + source enum (KAN-52)

Adds `consent_scope` to the three contact-channel tables (customer_phone /
customer_email / customer_address) and narrows `consent_source` from free
text to the closed enum `form` / `counter` / `web` / `phone`.

`consent_scope` is NULL on every existing row — NULL MEANS `marketing`,
which is what the bare `granted` flag has meant until now.

`consent_source` MIGRATION HONESTY: values that already match an enum
member (case-insensitively, trimmed) are kept; everything else is set NULL
and REPORTED — the distinct unmapped values and their row counts are
printed. A free string is not guessed at.

Revision ID: 3c8f2a6b1e40
Revises: 2b7e1d4a9c30
Create Date: 2026-09-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c8f2a6b1e40"
down_revision: Union[str, Sequence[str], None] = "2b7e1d4a9c30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("customer_phone", "customer_email", "customer_address")
_SCOPE = sa.Enum("marketing", "invoicing", "service", name="consentscope", native_enum=False, length=16)
_SOURCE_MEMBERS = ("form", "counter", "web", "phone")


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        op.add_column(table, sa.Column("consent_scope", _SCOPE, nullable=True))

        # Keep exact (case-insensitive, trimmed) matches; NULL + report the rest.
        unmapped = bind.execute(
            sa.text(
                f"SELECT consent_source, count(*) FROM {table} "
                "WHERE consent_source IS NOT NULL AND lower(trim(consent_source)) NOT IN :members "
                "GROUP BY consent_source"
            ).bindparams(sa.bindparam("members", _SOURCE_MEMBERS, expanding=True))
        ).all()
        if unmapped:
            rendered = ", ".join(f"{v!r}={n}" for v, n in unmapped)
            print(
                f"KAN-52 REPORT: {table}.consent_source — {sum(n for _, n in unmapped)} row(s) with unmapped "
                f"values set to NULL (distinct: {rendered}). Not guessed at (FR-23 §1)."
            )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET consent_source = lower(trim(consent_source)) "
                "WHERE consent_source IS NOT NULL AND lower(trim(consent_source)) IN :members"
            ).bindparams(sa.bindparam("members", _SOURCE_MEMBERS, expanding=True))
        )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET consent_source = NULL "
                "WHERE consent_source IS NOT NULL AND consent_source NOT IN :members"
            ).bindparams(sa.bindparam("members", _SOURCE_MEMBERS, expanding=True))
        )
        op.alter_column(table, "consent_source", type_=sa.String(length=16), existing_nullable=True)


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "consent_source", type_=sa.String(length=100), existing_nullable=True)
        op.drop_column(table, "consent_scope")

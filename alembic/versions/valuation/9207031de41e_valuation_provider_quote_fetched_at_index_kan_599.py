"""index valuation_provider_quote.fetched_at (KAN-599)

The daily retention purge (`valuation.provider_quotes.purge_unused`,
`app/valuation/services/provider_quote_retention.py`) reads the quotes
fetched more than ten years ago that no valuation references; this index
serves that range. DDL only, no data step. A plain (transactional)
`CREATE INDEX`: the table is days old (KAN-552) and small on every
environment, so it is neither large nor hot (migrations rule on
`CONCURRENTLY`).

Revision ID: 9207031de41e
Revises: 854d8cd3e4c7
Create Date: 2026-11-13 17:26:30.720404

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9207031de41e'
down_revision: Union[str, Sequence[str], None] = '854d8cd3e4c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        op.f('ix_valuation_provider_quote_fetched_at'), 'valuation_provider_quote', ['fetched_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_valuation_provider_quote_fetched_at'), table_name='valuation_provider_quote')

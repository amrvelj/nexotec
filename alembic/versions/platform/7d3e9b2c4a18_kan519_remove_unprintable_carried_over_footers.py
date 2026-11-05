"""KAN-519 — a carried-over footer that holds `{{` or `}}` is removed
(Anto, 2026-11-03, option C)

KAN-208's migration `ec4559d84ad3` copied each legacy
`document_template.footer_text_*` into three dealer standard texts of its
Betrieb (`invoice.footer.carried_over`, and `offer.footer.carried_over` /
`contract.footer.carried_over` as the Betrieb's **default** offer and
contract footer) as raw text, without the placeholder check a saved text
passes (`validate_body`). A legacy footer was free text with no placeholders
at all, so a `{{` or `}}` in it is never a token: it is a stray brace that
the renderer reads as a malformed placeholder, and every offer and contract
that Betrieb prints in that language fails with 422
`standard_text_placeholder_unresolved` (A-RULE-4).

Anto's ruling: remove such a footer. Printing works again at once — the
document falls back to the platform seed's footer — and the Betrieb retypes
its own in Dealer Administration › Standard texts, where the check runs.
Only the three carried-over codes are touched, in the language whose text
holds the brace; a text a Betrieb saved itself has passed `validate_body`
and is never touched.

It runs after `ec4559d84ad3` on every environment: where that one has run
already, it removes what it copied; where production data is still to be
migrated, the same upgrade copies and then removes, so nothing unprintable
ever reaches a print. Each removed row is reported on the upgrade's output
(`KAN-519 REPORT`, its Betrieb, code and language) so the Betrieb can be told.

Downgrade is deliberately a no-op: the removed text is not kept anywhere,
and putting an unprintable footer back would break printing again.

Revision ID: 7d3e9b2c4a18
Revises: 2b478e186431
Create Date: 2026-11-05 05:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "7d3e9b2c4a18"
down_revision: str | None = "2b478e186431"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The three codes ec4559d84ad3 wrote, written out: a migration never reads the
# application's declaration.
_CARRIED_OVER_CODES = (
    "invoice.footer.carried_over",
    "offer.footer.carried_over",
    "contract.footer.carried_over",
)

_WHERE = (
    "source = 'dealer' AND slot = 'footer' AND code IN ("
    + ", ".join(f"'{code}'" for code in _CARRIED_OVER_CODES)
    + ") AND (strpos(body, '{{') > 0 OR strpos(body, '}}') > 0)"
)


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute(f"DELETE FROM standard_text WHERE {_WHERE}")
        op.execute("-- KAN-519 report skipped: it reads rows and needs a live database")
        return
    removed = (
        op.get_bind()
        .execute(sa.text(f"DELETE FROM standard_text WHERE {_WHERE} RETURNING dealership_id, code, language"))
        .all()
    )
    for dealership_id, code, language in sorted(removed, key=lambda row: (str(row[0]), row[1], row[2])):
        print(f"KAN-519 REPORT: removed {code} ({language}) of Betrieb {dealership_id}: it held a stray brace")
    print(f"KAN-519: {len(removed)} carried-over footer text(s) removed")


def downgrade() -> None:
    # Deliberately a no-op (see the docstring): the removed footers are gone.
    pass

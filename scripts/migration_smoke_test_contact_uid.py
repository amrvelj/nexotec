"""The migration smoke test's contact UID (KAN-819, CI only), split from
`scripts/seed_migration_smoke_test.py` like `migration_smoke_test_catalogue.py`
(KAN-688: the source-file ratchet scans `scripts/` too).

The seeded contact gets an encrypted UID, so the backfill of
`contact.tax_id_search_key` (customer chain 9e4b7c2d1a58) runs on a real row
when the CI job upgrades from main's heads; `verify_migration_smoke_test_seed.py`
checks the key equals the application's. Raw SQL on whichever schema is live:
it imports no bounded context, writes the contact table only and never commits
— the seed's one commit does. The UID is a fixture, not a real one.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.blind_index import uid_search_key
from app.core.types import GUID, EncryptedString

SMOKE_TEST_CONTACT_UID = "CHE-123.456.788 MWST"


def seed_contact_uid(db: Session, *, contact_id: uuid.UUID) -> None:
    """The UID on the seeded contact. A schema that already has
    `tax_id_search_key` (this branch's heads) gets the key the model would
    write; main's has no such column, and the upgrade's backfill writes it."""

    tables = set(inspect(db.get_bind()).get_table_names())
    table_name = "contact" if "contact" in tables else "customer"
    live = {column["name"] for column in inspect(db.get_bind()).get_columns(table_name)}
    if "tax_id" not in live:
        return
    columns = [sa.column("id", GUID()), sa.column("tax_id", EncryptedString())]
    values: dict[str, object] = {"tax_id": SMOKE_TEST_CONTACT_UID}
    if "tax_id_search_key" in live:
        columns.append(sa.column("tax_id_search_key", sa.String()))
        values["tax_id_search_key"] = uid_search_key(SMOKE_TEST_CONTACT_UID)
    table = sa.table(table_name, *columns)
    db.execute(table.update().where(table.c.id == contact_id).values(values))

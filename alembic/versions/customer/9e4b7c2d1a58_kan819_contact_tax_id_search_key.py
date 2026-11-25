"""KAN-819: contact.tax_id_search_key, the keyed blind index of the UID

The contact search could not find a business contact by its UID: `tax_id` is
an `EncryptedString` (random nonce), which SQL cannot compare. This adds
`tax_id_search_key` — an HMAC-SHA256 of the UID in its canonical spelling
(`CHE` and nine digits), under a key derived from the Fernet key that already
protects `tax_id` — and fills it for every contact holding a UID. From here on
the model writes it on every assignment of `tax_id` (app/contact/models/
contact.py); its index is built by the next revision, concurrently.

Data step (skipped under `--sql`, KAN-174): each `tax_id` is decrypted with
the application's key (alembic/env.py has loaded Settings, which has no
default for it), canonicalised and keyed with a frozen copy of
app/core/blind_index.py's rule (tests/contact/test_kan819_contact_search_by_uid.py
holds the two equal). A value that does not decrypt or is no UID keeps the key
NULL and is counted, never named: the UIDs and the ids stay out of the log.
If not one encrypted value decrypts, the key is wrong, not the data, and the
upgrade stops.

Downgrade drops the column; nothing else held it.

Revision ID: 9e4b7c2d1a58
Revises: f0127c9deaf6
Create Date: 2026-11-25 08:00:00.000000

"""

import hashlib
import hmac
import re
from collections.abc import Sequence

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "9e4b7c2d1a58"
down_revision: str | Sequence[str] | None = "f0127c9deaf6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = "KAN-819: HMAC-SHA256 of the canonical UID in tax_id (app/core/blind_index.py); kept by the model."

# Frozen copy of app/core/blind_index.py (2026-11-25). Never edited: a change
# of the rule is a new data migration.
KEY_LABEL = b"nexotec/contact.tax_id_search_key/v1"
UID_SEARCH_RE = re.compile(
    r"^(?:CHE)?[\s.\-]*([0-9]{3})[\s.\-]?([0-9]{3})[\s.\-]?([0-9]{3})(?:\s*(?:MWST|TVA|IVA))?$",
    re.IGNORECASE,
)

_contact = sa.table(
    "contact",
    sa.column("id", sa.Uuid()),
    sa.column("tax_id", sa.Text()),
    sa.column("tax_id_search_key", sa.String(64)),
)


def search_key(plaintext: str, encryption_key: str) -> str | None:
    """The frozen rule: the hex HMAC of `CHE` + nine digits, or None."""

    match = UID_SEARCH_RE.fullmatch(plaintext.strip())
    if match is None:
        return None
    secret = hmac.new(encryption_key.encode("ascii"), KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(secret, ("CHE" + "".join(match.groups())).encode("ascii"), hashlib.sha256).hexdigest()


def _encryption_key() -> str:
    from app.core.config import get_settings

    return get_settings().tax_id_encryption_key


def _backfill() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-819 tax_id_search_key backfill skipped: it reads rows and needs a live database")
        return
    conn = op.get_bind()
    key = _encryption_key()
    fernet = Fernet(key.encode("ascii"))
    rows = conn.execute(sa.select(_contact.c.id, _contact.c.tax_id).where(_contact.c.tax_id.is_not(None))).all()
    keyed = undecryptable = not_a_uid = 0
    for row_id, ciphertext in rows:
        try:
            plaintext = fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken:
            undecryptable += 1
            continue
        value = search_key(plaintext, key)
        if value is None:
            not_a_uid += 1
            continue
        conn.execute(sa.update(_contact).where(_contact.c.id == row_id).values(tax_id_search_key=value))
        keyed += 1
    if rows and undecryptable == len(rows):
        raise RuntimeError(
            f"KAN-819: none of the {len(rows)} encrypted tax_id value(s) decrypts with the configured key - "
            "the key is wrong, not the data. Nothing was changed."
        )
    print(
        f"KAN-819: {keyed} contact(s) keyed, {not_a_uid} holding no UID, {undecryptable} not decrypting "
        f"(of {len(rows)} with a tax_id)"
    )


def upgrade() -> None:
    op.add_column("contact", sa.Column("tax_id_search_key", sa.String(64), nullable=True, comment=_COMMENT))
    _backfill()


def downgrade() -> None:
    op.drop_column("contact", "tax_id_search_key")

"""Keyed blind index for a Swiss UID held encrypted (KAN-819).

A contact's UID (`contact.tax_id`) is an `EncryptedString`: Fernet with a
random nonce, so SQL can never compare it with what a user typed. The contact
search finds a business contact by its UID through a second column beside the
encrypted one, `tax_id_search_key`: an HMAC-SHA256 of the UID in one canonical
spelling, under a key only the application holds. Equal UIDs give equal keys,
so the search is one indexed equality; without the key the column says
nothing — the UID space (10^8 numbers) is small enough to enumerate, which is
why a plain hash would not do.

**The key** is derived from `Settings.tax_id_encryption_key`, the key that
already protects the column, with HMAC under a fixed label: one secret to hold,
and the derived key is used for nothing else. Rotating that key re-encrypts
every `tax_id`; the same job recomputes the search keys (`uid_search_key` on
each decrypted value).

**The canonical spelling** is `CHE` and the nine digits: `CHE-123.456.789`,
`CHE123456789`, `che 123 456 789` and `CHE-123.456.789 MWST` (TVA, IVA) are one
UID. The check digit is not judged here: a stored UID passed `SwissUid`, and a
search for a mistyped one simply finds nothing.

Only whole UIDs match: a blind index cannot answer «contains 456». The search
text and the UID never reach a log line from here.

The migration that backfilled the column (`alembic/versions/customer/
9e4b7c2d1a58_kan819_contact_tax_id_search_key.py`) carries a frozen copy of
this rule; `tests/contact/test_kan819_contact_search_by_uid.py` holds the two
equal.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from functools import lru_cache

from app.core.config import get_settings

# The label the search key is derived under. Changing it changes every key:
# it is a migration, never an edit.
_KEY_LABEL = b"nexotec/contact.tax_id_search_key/v1"

# «CHE», then nine digits in groups of three, each group optionally preceded by
# a dot, hyphen or space; an optional VAT suffix in one of the three languages.
# [0-9], not \d: \d also matches other scripts' digits (KAN-197). The «CHE» is
# optional, so «123.456.789» finds the UID too.
_UID_SEARCH_RE = re.compile(
    r"^(?:CHE)?[\s.\-]*([0-9]{3})[\s.\-]?([0-9]{3})[\s.\-]?([0-9]{3})(?:\s*(?:MWST|TVA|IVA))?$",
    re.IGNORECASE,
)


def canonical_uid(text: str | None) -> str | None:
    """`CHE` and the nine digits of a UID in any common spelling, or None when
    the text is no UID."""

    if not text:
        return None
    match = _UID_SEARCH_RE.fullmatch(text.strip())
    if match is None:
        return None
    return "CHE" + "".join(match.groups())


@lru_cache
def _search_key_secret() -> bytes:
    return hmac.new(
        get_settings().tax_id_encryption_key.encode("ascii"), _KEY_LABEL, hashlib.sha256
    ).digest()


def uid_search_key(text: str | None) -> str | None:
    """The blind-index value for a UID (hex HMAC-SHA256 of its canonical
    spelling), or None when the text is no UID."""

    canonical = canonical_uid(text)
    if canonical is None:
        return None
    return hmac.new(_search_key_secret(), canonical.encode("ascii"), hashlib.sha256).hexdigest()

"""Canonical secret-field redaction (WP-2 PR-4 — "secrets are never
logged, redact at the logging boundary").

Single source of truth for which field NAMES are secrets.
app.customer.services.customer and app.platform.services.dealership each had
their own independently maintained `_SECRET_FIELDS = {"tax_id"}` before
this module existed — identical today, but two copies of the same set is
exactly how one of them silently stops matching the other as fields get
added. app.core.observability's JSONLogFormatter uses this same set, so a
field redacted from the audit log can't leak back in through an ordinary
log line that happens to carry it in `extra={}`.

Field NAMES only — this has no idea what a value even is, so it can't
accidentally redact something that merely looks sensitive.
"""

REDACTED_PLACEHOLDER = "***redacted***"

SECRET_FIELDS = frozenset({"tax_id"})


def is_secret_field(field: str) -> bool:
    return field in SECRET_FIELDS

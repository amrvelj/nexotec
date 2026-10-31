"""The closed placeholder registry and the one placeholder renderer of the
standard-text library (KAN-208; PRD "Standard texts" › Placeholders,
A-RULE-4, FR-A-07).

- **Closed.** The eight tokens below are the whole vocabulary. A token that
  is not one of them is *unknown*; a known token outside the target's
  validity is *not valid here*; `{{` or `}}` without a well-formed token is
  *malformed*. A save refuses all three (422
  `standard_text_placeholder_invalid`).
- **Per target.** Which tokens a text may use depends on where it is used —
  its (category, applies_to): an offer document has no payment due date, an
  e-mail to a customer has a salutation. `TARGETS` is that table, and the
  slots each target has; the editor offers only what it lists (FR-A-07).
- **A-RULE-4 — a token that cannot be resolved fails the render.** Never
  blank, never the raw token: `render_body` raises 422
  `standard_text_placeholder_unresolved`, naming every token at fault, when
  a token is unknown, not valid for the target, malformed, or its value is
  missing or blank.

The renderer substitutes values as plain text; escaping for HTML is the
document renderer's job (`document_render._esc`), as for every other string.
"""

import datetime as dt
import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.errors import UnprocessableEntityError
from app.core.i18n import SwissLanguage, format_date_ch
from app.platform.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.models.standard_text_delegation import StandardTextCategory

PLACEHOLDER_INVALID = "standard_text_placeholder_invalid"
PLACEHOLDER_UNRESOLVED = "standard_text_placeholder_unresolved"


class Placeholder(str, enum.Enum):
    """The PRD's closed token set."""

    DEALERSHIP_NAME = "dealership.name"
    LOCATION_PHONE = "location.phone"
    CUSTOMER_SALUTATION = "customer.salutation"
    VEHICLE_MAKE_MODEL = "vehicle.make_model"
    DOCUMENT_NUMBER = "document.number"
    DOCUMENT_DATE = "document.date"
    USER_FULL_NAME = "user.full_name"
    PAYMENT_DUE_DATE = "payment.due_date"


# The two tokens platform resolves itself from its own records: the Betrieb's
# name (trade name, else legal name — the letterhead's rule) and the phone
# (the location's own once a location carries one; `location` has no phone
# column today, so the Betrieb's — the PRD's location → dealership fallback).
# Every other token is the calling module's fact and arrives in `values`.
PLATFORM_RESOLVED = frozenset({Placeholder.DEALERSHIP_NAME, Placeholder.LOCATION_PHONE})


@dataclass(frozen=True)
class TextTarget:
    category: StandardTextCategory
    applies_to: StandardTextAppliesTo
    slots: tuple[StandardTextSlot, ...]
    placeholders: frozenset[Placeholder]


_P = Placeholder
_S = StandardTextSlot
_A = StandardTextAppliesTo
_C = StandardTextCategory

# What the offer and contract documents can supply today: the seller is the
# responsible seller (owner), the vehicle its label, the number and date the
# document's own. No salutation — Sales holds no letter opening (a customer's
# salutation is Customers' to form) — and no payment due date.
_SALES_DOCUMENT = frozenset(
    {_P.DEALERSHIP_NAME, _P.LOCATION_PHONE, _P.VEHICLE_MAKE_MODEL, _P.DOCUMENT_NUMBER, _P.DOCUMENT_DATE, _P.USER_FULL_NAME}
)
_INVOICE = frozenset(set(_P))
_MESSAGE = frozenset(
    {
        _P.DEALERSHIP_NAME,
        _P.LOCATION_PHONE,
        _P.CUSTOMER_SALUTATION,
        _P.VEHICLE_MAKE_MODEL,
        _P.USER_FULL_NAME,
    }
)
_DOCUMENT_MESSAGE = _MESSAGE | {_P.DOCUMENT_NUMBER, _P.DOCUMENT_DATE}
_WORKSHOP = frozenset(
    {
        _P.DEALERSHIP_NAME,
        _P.LOCATION_PHONE,
        _P.CUSTOMER_SALUTATION,
        _P.VEHICLE_MAKE_MODEL,
        _P.DOCUMENT_NUMBER,
        _P.DOCUMENT_DATE,
        _P.USER_FULL_NAME,
    }
)
_NOTE = frozenset({_P.DEALERSHIP_NAME, _P.USER_FULL_NAME, _P.DOCUMENT_DATE})

_DOC_SLOTS = (_S.HEADER, _S.INTRO, _S.TERMS, _S.CLOSING, _S.FOOTER)

TARGETS: tuple[TextTarget, ...] = (
    TextTarget(_C.DOCUMENT, _A.OFFER, _DOC_SLOTS, _SALES_DOCUMENT),
    TextTarget(_C.DOCUMENT, _A.CONTRACT, _DOC_SLOTS, _SALES_DOCUMENT),
    TextTarget(_C.DOCUMENT, _A.INVOICE, _DOC_SLOTS, _INVOICE),
    TextTarget(_C.EMAIL, _A.OFFER, (_S.BODY,), _DOCUMENT_MESSAGE),
    TextTarget(_C.EMAIL, _A.INVOICE, (_S.BODY,), _DOCUMENT_MESSAGE | {_P.PAYMENT_DUE_DATE}),
    TextTarget(_C.EMAIL, _A.APPOINTMENT_REMINDER, (_S.BODY,), _MESSAGE),
    TextTarget(_C.EMAIL, _A.VEHICLE_READY, (_S.BODY,), _MESSAGE),
    TextTarget(_C.SMS, _A.APPOINTMENT_REMINDER, (_S.BODY,), _MESSAGE),
    TextTarget(_C.SMS, _A.VEHICLE_READY, (_S.BODY,), _MESSAGE),
    TextTarget(_C.WHATSAPP, _A.APPOINTMENT_REMINDER, (_S.BODY,), _MESSAGE),
    TextTarget(_C.WHATSAPP, _A.VEHICLE_READY, (_S.BODY,), _MESSAGE),
    TextTarget(_C.INTERNAL_NOTE, _A.CUSTOMER_NOTE, (_S.BODY,), _NOTE),
    TextTarget(_C.WORKSHOP, _A.WORKSHOP_ORDER, (_S.INTRO, _S.BODY, _S.FOOTER), _WORKSHOP),
)

_BY_KEY = {(t.category, t.applies_to): t for t in TARGETS}


def target_for(category: StandardTextCategory, applies_to: StandardTextAppliesTo) -> TextTarget | None:
    return _BY_KEY.get((category, applies_to))


# One well-formed token: `{{ name }}`, whitespace inside the braces allowed.
_TOKEN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def _problems(body: str, target: TextTarget) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for name in dict.fromkeys(tokens_in(body)):
        try:
            token = Placeholder(name)
        except ValueError:
            found.append({"token": name, "problem": "unknown"})
            continue
        if token not in target.placeholders:
            found.append({"token": name, "problem": "not_valid_here"})
    leftover = _TOKEN.sub("", body)
    if "{{" in leftover or "}}" in leftover:
        found.append({"token": "", "problem": "malformed"})
    return found


def validate_body(body: str, target: TextTarget) -> None:
    """A save's check (FR-A-07 enforced server-side): only tokens the
    target lists, nothing malformed."""

    problems = _problems(body, target)
    if problems:
        raise UnprocessableEntityError(
            "The text uses placeholders that are not allowed here.",
            details={"reason": PLACEHOLDER_INVALID, "placeholders": problems},
        )


def tokens_in(body: str) -> list[str]:
    return [match.group(1) for match in _TOKEN.finditer(body)]


def render_body(
    body: str,
    *,
    target: TextTarget,
    values: Mapping[str, str | None],
    context: Mapping[str, Any] | None = None,
) -> str:
    """Substitute every token, or fail the render naming all at fault
    (A-RULE-4). `values` maps a token name to its value; a missing key,
    None or a blank string is unresolvable."""

    problems = _problems(body, target)
    for name in dict.fromkeys(tokens_in(body)):
        if any(p["token"] == name for p in problems):
            continue
        value = values.get(name)
        if value is None or not str(value).strip():
            problems.append({"token": name, "problem": "unresolved"})
    if problems:
        raise UnprocessableEntityError(
            "A standard text could not be rendered: a placeholder cannot be resolved.",
            details={"reason": PLACEHOLDER_UNRESOLVED, "placeholders": problems, **(context or {})},
        )
    return _TOKEN.sub(lambda match: str(values[match.group(1)]), body)


# --- sample data for the editor's preview (FR-A-07) -------------------------------

_SAMPLE_SALUTATION = {
    SwissLanguage.DE: "Sehr geehrte Frau Muster",
    SwissLanguage.FR: "Madame",
    SwissLanguage.IT: "Gentile Signora Rossi",
    SwissLanguage.EN: "Dear Ms Smith",
}


def sample_values(language: SwissLanguage, *, today: dt.date) -> dict[str, str]:
    """Sample values for the tokens a module would supply. The preview fills
    the two platform tokens from the Betrieb's own record, as a render does."""

    return {
        Placeholder.CUSTOMER_SALUTATION.value: _SAMPLE_SALUTATION[language],
        Placeholder.VEHICLE_MAKE_MODEL.value: "VW Golf 2.0 TDI",
        Placeholder.DOCUMENT_NUMBER.value: "O-000123",
        Placeholder.DOCUMENT_DATE.value: format_date_ch(today),
        Placeholder.USER_FULL_NAME.value: "Anna Beispiel",
        Placeholder.PAYMENT_DUE_DATE.value: format_date_ch(today + dt.timedelta(days=30)),
    }

"""Reusable field validators shared across entity schemas (Dealer today;
Customer/Vehicle in later issues will reuse the same Swiss address/phone
shape per the addendum).
"""

import re
from typing import Annotated

from pydantic import AfterValidator

from app.core.constants import CANTON_CODES

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_POSTAL_CODE_RE = re.compile(r"^\d{4}$")


def _validate_canton(value: str) -> str:
    if value not in CANTON_CODES:
        raise ValueError(f"'{value}' is not a valid Swiss canton code.")
    return value


def _validate_e164_phone(value: str) -> str:
    if not _E164_RE.match(value):
        raise ValueError("Phone number must be in E.164 format, e.g. '+41791234567'.")
    return value


def _validate_postal_code(value: str) -> str:
    if not _POSTAL_CODE_RE.match(value):
        raise ValueError("Postal code must be 4 numeric digits.")
    return value


CantonCode = Annotated[str, AfterValidator(_validate_canton)]
E164Phone = Annotated[str, AfterValidator(_validate_e164_phone)]
SwissPostalCode = Annotated[str, AfterValidator(_validate_postal_code)]

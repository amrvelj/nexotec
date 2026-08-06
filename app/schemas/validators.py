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
# ISO 3779: 17 chars, uppercase alphanumeric excluding I/O/Q (visually
# confusable with 1/0). No US NHTSA check-digit (FMVSS 115 position-9
# algorithm) — that's US-specific, not universally applicable to Swiss/EU
# VINs ("ISO 3779 check-digit where applicable" per the Swiss addendum).
# Malformed input is rejected outright, never silently normalized/uppercased.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


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


def _validate_vin(value: str) -> str:
    if not _VIN_RE.match(value):
        raise ValueError(
            "VIN must be exactly 17 uppercase alphanumeric characters (excluding I, O, Q), no whitespace."
        )
    return value


CantonCode = Annotated[str, AfterValidator(_validate_canton)]
E164Phone = Annotated[str, AfterValidator(_validate_e164_phone)]
SwissPostalCode = Annotated[str, AfterValidator(_validate_postal_code)]
Vin = Annotated[str, AfterValidator(_validate_vin)]

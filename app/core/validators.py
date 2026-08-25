"""Reusable field validators shared across entity schemas (Dealership today;
Customer/Vehicle reuse the same Swiss address/phone shape per the addendum).
"""

import re
from typing import Annotated

from pydantic import AfterValidator

from app.core.constants import CANTON_CODES

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_POSTAL_CODE_RE = re.compile(r"^\d{4}$")
# Swiss addresses routinely carry a letter suffix ("12a", "4B"). The Customer
# PRD source sheet said "only numbers allowed", which rejects real addresses
# — Customer PRD decision D-10 relaxes it to digits + optional short suffix.
_HOUSE_NUMBER_RE = re.compile(r"^\d{1,5}\s?[A-Za-z]{0,3}$")
# Non-CH postal codes have no single shape (DE 5 digits, FR 5, IT 5, AT 4,
# LI 4, NL "1234 AB", UK alphanumeric). Validate only that it is plausible
# rather than inventing per-country rules we cannot maintain (D-11).
_FOREIGN_POSTAL_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{1,11}$")
# ISO 3779: 17 chars, uppercase alphanumeric excluding I/O/Q (visually
# confusable with 1/0). No US NHTSA check-digit (FMVSS 115 position-9
# algorithm) — that's US-specific, not universally applicable to Swiss/EU
# VINs ("ISO 3779 check-digit where applicable" per the Swiss addendum).
# Malformed input is rejected outright, never silently normalized/uppercased.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Swiss UID (Unternehmens-Identifikationsnummer), format CHE-123.456.789.
# The 9th digit is a mod-11 check digit over the first 8 (Customer PRD D-16):
# weighted sum, remainder from 11; a computed check digit of 10 is never
# issued, and 11 maps to 0.
_UID_RE = re.compile(r"^CHE-\d{3}\.\d{3}\.\d{3}$")
_UID_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)

_DEFAULT_COUNTRY_CALLING_CODE = "41"


def uid_check_digit(first_eight: str) -> int | None:
    """Return the valid check digit for the first 8 UID digits, or None when
    the combination is unissuable (computed remainder yields 10). Exposed so
    tests and data-migration tooling can generate valid UIDs rather than
    hard-coding magic numbers.
    """

    total = sum(int(d) * w for d, w in zip(first_eight, _UID_WEIGHTS))
    check = 11 - (total % 11)
    if check == 10:
        return None
    return 0 if check == 11 else check


def normalise_phone(value: str | None) -> str:
    """Reduce a phone number to comparable digits so that '079 123 45 67',
    '+41791234567' and '0041791234567' all resolve to the same string
    (Customer PRD FR-01). Used for search and duplicate matching only —
    the stored value stays exactly as the caller supplied it in E.164.
    """

    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return _DEFAULT_COUNTRY_CALLING_CODE + digits[1:]
    return digits


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


def validate_postal_code_for_country(value: str, country: str) -> str:
    """Postal-code rules depend on the country (D-11). Enforcing the Swiss
    4-digit rule unconditionally blocked cross-border customers (DE/FR/IT/
    AT/LI), who are routine for Swiss dealerships near a border.
    """

    if country.upper() == "CH":
        return _validate_postal_code(value)
    if not _FOREIGN_POSTAL_CODE_RE.match(value):
        raise ValueError(f"'{value}' is not a plausible postal code for country '{country}'.")
    return value


def _validate_house_number(value: str) -> str:
    if not _HOUSE_NUMBER_RE.match(value):
        raise ValueError("House number must be digits with an optional short letter suffix, e.g. '42' or '12a'.")
    return value


def _validate_vin(value: str) -> str:
    if not _VIN_RE.match(value):
        raise ValueError(
            "VIN must be exactly 17 uppercase alphanumeric characters (excluding I, O, Q), no whitespace."
        )
    return value


def _validate_swiss_uid(value: str) -> str:
    normalised = value.strip().upper()
    if not _UID_RE.match(normalised):
        raise ValueError("UID must be in the format 'CHE-123.456.789'.")
    digits = normalised[4:].replace(".", "")
    expected = uid_check_digit(digits[:8])
    if expected is None or expected != int(digits[8]):
        raise ValueError(f"'{value}' is not a valid Swiss UID — the check digit does not match.")
    return normalised


CantonCode = Annotated[str, AfterValidator(_validate_canton)]
E164Phone = Annotated[str, AfterValidator(_validate_e164_phone)]
HouseNumber = Annotated[str, AfterValidator(_validate_house_number)]
SwissPostalCode = Annotated[str, AfterValidator(_validate_postal_code)]
SwissUid = Annotated[str, AfterValidator(_validate_swiss_uid)]
Vin = Annotated[str, AfterValidator(_validate_vin)]

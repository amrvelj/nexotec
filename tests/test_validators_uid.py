"""KAN-197 (D-A-22): the pure UID and VAT-number rules in app.core.validators."""

import pytest
from pydantic import BaseModel, ValidationError

from app.core.validators import SwissVatNumber, uid_check_digit, validate_swiss_uid


class _VatBody(BaseModel):
    vat_number: SwissVatNumber


def test_valid_uid_is_returned_normalised():
    assert validate_swiss_uid("CHE-123.456.788") == "CHE-123.456.788"
    assert validate_swiss_uid("  che-123.456.788 ") == "CHE-123.456.788"


@pytest.mark.parametrize("value", ["CHE-123.456.789", "CHE-987.654.321", "CHE-999.999.999"])
def test_invalid_check_digit_is_rejected(value):
    with pytest.raises(ValueError, match="check digit"):
        validate_swiss_uid(value)


@pytest.mark.parametrize(
    "value",
    [
        "123.456.788",
        "CHE-12345678",
        "CHE-123.456.78",
        "CHE 123.456.788",
        "",
        "CHE-abc.def.ghi",
        # Full-width digits: \d would accept them and a second spelling of
        # the same UID would slip past the unique index (KAN-197 review).
        "CHE-\uff11\uff12\uff13.456.788",
    ],
)
def test_malformed_uid_is_rejected(value):
    with pytest.raises(ValueError, match="format"):
        validate_swiss_uid(value)


def test_check_digit_helper_marks_unissuable_combinations():
    # 11 - (total % 11) == 10 has no valid check digit; a UID with those
    # first eight digits is never issued.
    unissuable = next(f"{n:08d}" for n in range(10_000_000, 10_001_000) if uid_check_digit(f"{n:08d}") is None)
    with pytest.raises(ValueError):
        validate_swiss_uid(f"CHE-{unissuable[:3]}.{unissuable[3:6]}.{unissuable[6:]}0")


@pytest.mark.parametrize(
    "value", ["CHE-123.456.788 MWST", "che-123.456.788 mwst", "CHE-123.456.788MWST", " CHE-123.456.788  MWST "]
)
def test_vat_number_is_normalised_to_uid_space_suffix(value):
    assert _VatBody(vat_number=value).vat_number == "CHE-123.456.788 MWST"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("CHE-123.456.788 TVA", "CHE-123.456.788 TVA"), ("che-123.456.788 iva", "CHE-123.456.788 IVA")],
)
def test_vat_number_accepts_the_french_and_italian_suffixes_as_entered(value, expected):
    # Anto, 2026-10-17: MWST, TVA and IVA are all official; stored as entered.
    assert _VatBody(vat_number=value).vat_number == expected


@pytest.mark.parametrize("value", ["CHE-123.456.789 MWST", "CHE-123.456.788", "CHE-123.456.788 VAT", "123.456.788 MWST"])
def test_vat_number_with_bad_uid_or_suffix_is_rejected(value):
    with pytest.raises(ValidationError):
        _VatBody(vat_number=value)

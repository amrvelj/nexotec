"""KAN-770: every pattern of `app/core/validators.py` judges the whole value. Python's `$`
also matches just before a final newline, so `re.match(r"^…$")` let «9112300123\\n» pass as
a chassis number in the one spelling and a VIN with a trailing newline pass as valid."""

import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core import validators
from app.core.validators import (
    E164Phone,
    HouseNumber,
    NormalisedVin,
    NormalisedVinOrChassisNumber,
    SwissPostalCode,
    Vin,
    is_chassis_number_spelling,
    is_valid_vin,
)

VIN = "WVWZZZ1KZAW569001"


def test_a_stored_value_with_a_trailing_newline_is_not_the_one_spelling():
    assert is_valid_vin(VIN) and is_chassis_number_spelling("9112300123")
    assert not is_valid_vin(f"{VIN}\n")
    assert not is_chassis_number_spelling("9112300123\n")
    assert not is_chassis_number_spelling(f"{VIN}\n")


@pytest.mark.parametrize(
    ("annotation", "value"),
    [(Vin, f"{VIN}\n"), (E164Phone, "+41791234567\n"), (SwissPostalCode, "8001\n"), (HouseNumber, "12a\n")],
)
def test_a_validator_refuses_a_value_with_a_trailing_newline(annotation, value):
    with pytest.raises(ValidationError):
        TypeAdapter(annotation).validate_python(value)


def test_a_typed_vin_is_still_normalised_first():
    """No typed-input change for the VIN fields: whitespace, a newline included, is removed first."""

    assert TypeAdapter(NormalisedVin).validate_python(f" {VIN.lower()}\n") == VIN
    assert TypeAdapter(NormalisedVinOrChassisNumber).validate_python("911 230 0123\n") == "9112300123"


def test_no_pattern_in_the_validators_is_applied_with_match():
    """The idiom itself: `.match` with `$` is the defect; `.fullmatch` is the rule here."""

    source = Path(validators.__file__).read_text()
    assert not re.findall(r"_RE\.match\(", source)

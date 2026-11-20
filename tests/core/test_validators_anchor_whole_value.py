"""KAN-770: the VIN and chassis-number patterns judge the whole value. Python's `$` also
matches just before a final newline, so `re.match(r"^…$")` let «9112300123\\n» pass as a
chassis number in the one spelling and a VIN with a trailing newline pass as valid. The other
patterns of `app/core/validators.py` (and of `app/`) keep the idiom until KAN-775: a stored
value they accepted must still read (`DealershipAddress` validates on read)."""

import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core import validators
from app.core.validators import (
    NormalisedVin,
    NormalisedVinOrChassisNumber,
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


def test_the_vin_type_refuses_a_value_with_a_trailing_newline():
    """`Vin` guards vehicle-mdm's own, never-normalised fields: a trailing newline is refused there."""

    with pytest.raises(ValidationError):
        TypeAdapter(Vin).validate_python(f"{VIN}\n")


def test_a_typed_vin_is_still_normalised_first():
    """No typed-input change for the VIN fields: whitespace, a newline included, is removed first."""

    assert TypeAdapter(NormalisedVin).validate_python(f" {VIN.lower()}\n") == VIN
    assert TypeAdapter(NormalisedVinOrChassisNumber).validate_python("911 230 0123\n") == "9112300123"


def test_the_vin_and_chassis_patterns_are_applied_with_fullmatch():
    """The idiom itself: `.match` with `$` is the defect; `.fullmatch` is the rule for these two."""

    source = Path(validators.__file__).read_text()
    assert not re.findall(r"_(VIN|CHASSIS_NUMBER)_RE\.match\(", source)

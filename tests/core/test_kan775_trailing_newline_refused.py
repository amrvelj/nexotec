"""KAN-775: the anchored patterns of `app/core` judge the whole value.

Python's `$` also matches just before a final newline, so `re.match(r"^\\d{4}$", "8001\\n")`
was truthy: a value with a trailing newline passed as a phone number, a postal code, a number
filter or an object key. Each pattern is now applied with `fullmatch`
(`tests/architecture/test_anchored_patterns_judge_the_whole_value.py` holds the rule); each test
here is the input that passed before.

`HouseNumber` was the exception the ticket named: its `\\s?` between the digits and the suffix
accepted «12\\n» under `fullmatch` too — the character class decided that, not the anchor. KAN-778
made the separator a plain space; the test below is turned round.
"""

import pytest
import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError

from app.core.errors import UnprocessableEntityError
from app.core.list_query import ColumnSpec, _parse_value
from app.core.object_store import validate_key
from app.core.validators import (
    E164Phone,
    HouseNumber,
    Iban,
    SwissUid,
    SwissVatNumber,
    iban_is_valid,
    validate_postal_code_for_country,
)

VALID_UID = "CHE-123.456.788"


def test_a_phone_number_with_a_trailing_newline_is_refused():
    assert TypeAdapter(E164Phone).validate_python("+41791234567") == "+41791234567"
    with pytest.raises(ValidationError):
        TypeAdapter(E164Phone).validate_python("+41791234567\n")


@pytest.mark.parametrize(("value", "country"), [("8001\n", "CH"), ("10115\n", "DE"), ("1234 AB\n", "NL")])
def test_a_postal_code_with_a_trailing_newline_is_refused(value, country):
    assert validate_postal_code_for_country(value.rstrip("\n"), country) == value.rstrip("\n")
    with pytest.raises(ValueError):
        validate_postal_code_for_country(value, country)


def test_the_uid_vat_number_and_iban_are_normalised_before_the_pattern():
    """These three strip whitespace before they are judged, so a trailing newline never
    reached their pattern: the stored value is the clean spelling (no change in behaviour)."""

    assert TypeAdapter(SwissUid).validate_python(f"{VALID_UID}\n") == VALID_UID
    assert TypeAdapter(SwissVatNumber).validate_python(f"{VALID_UID} MWST\n") == f"{VALID_UID} MWST"
    assert TypeAdapter(Iban).validate_python("CH93 0076 2011 6238 5295 7\n") == "CH9300762011623852957"
    assert iban_is_valid("CH9300762011623852957\n")


@pytest.mark.parametrize("value", ["12\n", "12\ta", "12\na", "12\r", "12\u00a0a", "\uff11\uff12"])
def test_the_house_number_refuses_any_separator_but_a_plain_space(value):
    """KAN-778: «12\\n» and «12\\ta» passed through `\\s?`; full-width digits through `\\d`."""

    with pytest.raises(ValidationError):
        TypeAdapter(HouseNumber).validate_python(value)


@pytest.mark.parametrize("value", ["12", "12a", "12 a", "12 ABC", "99999"])
def test_the_house_number_still_takes_digits_with_an_optional_spaced_suffix(value):
    assert TypeAdapter(HouseNumber).validate_python(value) == value


@pytest.mark.parametrize(
    ("value_type", "operator", "raw"),
    [
        ("number", "equals", "42\n"),
        ("money", "equals", "19990.50\n"),
        ("number", "between", "1,2\n"),
        ("date", "inTheLastDays", "30\n"),
    ],
)
def test_a_list_filter_value_with_a_trailing_newline_is_refused(value_type, operator, raw):
    spec = ColumnSpec(api_name="field", column=sa.column("field"), value_type=value_type, label_key="k")
    _parse_value(spec, operator, raw.rstrip("\n"))
    with pytest.raises(UnprocessableEntityError) as refused:
        _parse_value(spec, operator, raw)
    assert refused.value.details["reason"] == "invalid_filter_value"


def test_an_object_key_with_a_trailing_newline_is_refused():
    assert validate_key("dealership/abc/logo.png") == "dealership/abc/logo.png"
    with pytest.raises(ValueError):
        validate_key("dealership/abc/logo.png\n")

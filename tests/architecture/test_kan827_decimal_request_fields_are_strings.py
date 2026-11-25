"""KAN-827: a decimal request field takes a decimal string, never a JSON number.

A JSON number is decoded as a float before pydantic turns it into a
`Decimal`, so `99999999999999.99` sent as a number arrives as
`99999999999999.98` — a Rappen lost without an error (ADR-215: money is exact
to the Rappen). Every `Decimal` field of a request body is therefore a
`DecimalStr` (`app/core/validators.py`): a string only, a JSON number is 422.

The walk reads the OpenAPI document, so it covers every request body the API
serves, whichever schema module declares it. A plain
`Decimal` field shows there as `anyOf: [number, string]` — that pair is the
signature this test refuses. A float field (a coordinate) is `number` alone
and is not a money field; it is not this test's business. Query parameters are
not walked: a URL is text, so a decimal query value reaches `Decimal(str)`
exactly and loses nothing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.core.validators import DecimalStr
from app.main import app


def _accepts_number_and_string(node: dict[str, Any]) -> bool:
    branches = node.get("anyOf") or node.get("oneOf") or []
    types = {branch.get("type") for branch in branches if isinstance(branch, dict)}
    return "number" in types and "string" in types


def _decimal_request_fields() -> list[str]:
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    found: set[str] = set()

    def walk(node: Any, where: str, seen: frozenset[str]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, where, seen)
            return
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name not in seen:
                walk(components[name], name, seen | {name})
            return
        if _accepts_number_and_string(node):
            found.add(where)
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for prop, schema in value.items():
                    walk(schema, f"{where}.{prop}", seen)
            elif isinstance(value, (dict, list)):
                walk(value, where, seen)

    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            if "requestBody" in operation:
                walk(operation["requestBody"], f"{method.upper()} {path} body", frozenset())
    return sorted(found)


def test_no_request_field_takes_a_decimal_as_a_json_number() -> None:
    offenders = _decimal_request_fields()
    assert offenders == [], (
        "These request fields accept a JSON number for a Decimal, which passes through a float "
        "and can lose a Rappen (KAN-827). Declare them `DecimalStr` (app/core/validators.py):\n  "
        + "\n  ".join(offenders)
    )


class _Body(BaseModel):
    amount: DecimalStr | None = Field(default=None, ge=0, max_digits=16, decimal_places=2)
    rates: list[DecimalStr] = []


def test_a_decimal_string_keeps_every_rappen() -> None:
    body = _Body.model_validate({"amount": "99999999999999.99"})
    assert body.amount == Decimal("99999999999999.99")


@pytest.mark.parametrize("number", [99999999999999.99, 12.5, 1000, True])
def test_a_json_number_is_refused_with_a_named_error(number: object) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as caught:
        _Body.model_validate({"amount": number})
    (error,) = caught.value.errors()
    assert error["type"] == "decimal_string_type"
    assert "string" in error["msg"]


def test_a_number_inside_a_list_is_refused_too() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as caught:
        _Body.model_validate({"rates": ["7.7", 8.1]})
    assert caught.value.errors()[0]["loc"] == ("rates", 1)


def test_the_field_constraints_still_apply() -> None:
    from pydantic import ValidationError

    for bad in ("-1", "1.234"):
        with pytest.raises(ValidationError):
            _Body.model_validate({"amount": bad})


def test_the_schema_shows_a_string_only() -> None:
    amount = _Body.model_json_schema(mode="validation")["properties"]["amount"]
    branches = amount["anyOf"]
    assert {branch.get("type") for branch in branches} == {"string", "null"}
    (string,) = [branch for branch in branches if branch.get("type") == "string"]
    assert string["format"] == "decimal"


@pytest.mark.parametrize("text", ["1e3", " 12 ", "12,5", "1'250.00", "+5", ".", "-", "NaN", "Infinity", "１２"])
def test_a_string_outside_the_advertised_pattern_is_refused(text: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as caught:
        _Body.model_validate({"amount": text})
    assert caught.value.errors()[0]["type"] == "decimal_string_format"


def test_the_schema_pattern_is_the_one_enforced() -> None:
    import re

    string = next(
        branch
        for branch in _Body.model_json_schema(mode="validation")["properties"]["amount"]["anyOf"]
        if branch.get("type") == "string"
    )
    pattern = re.compile(string["pattern"])
    for accepted in ("0", "12", "12.5", "12.", ".5", "99999999999999.99"):
        assert pattern.fullmatch(accepted)
        _Body.model_validate({"amount": accepted})
    assert not pattern.fullmatch("1e3")

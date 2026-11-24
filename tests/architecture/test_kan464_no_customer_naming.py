"""Customers are contacts — the tests that would have caught a half-rename (KAN-464 PR-4, ADR-210).

Anto ruled one register with roles (2026-10-26); PR-3 renamed the register,
its API and its events in one breaking change. These guards keep it whole:

- the API: no path, parameter, schema or field names «customer». Enum values
  may, but only the ones listed below, each with its reason;
- the events: no code in `app/` names a `customer.*` event except the old
  names kept until PR-5 (and the `{{customer.salutation}}` placeholder Anto
  kept). Every consumer of an old name has its twin on the new name, with the
  same handler — so an event published under the new name reaches everyone
  the old one did;
- every renamed event the contact context publishes is in the
  reconciliation's old-name map, and the old-name consumers are exactly the
  ones that map's names still feed.

The SPA's half (translated strings, routes) is
`frontend/apps/dms/src/architecture/no-customer-wording.test.ts`. KAN-464 PR-5
removes the old event names; that change empties `OLD_NAME_CONSUMERS` and the
reconciliation's entries below.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.contact.reconciliation import _BEFORE_THE_RENAME
from app.core.outbox_transport import Handler, InProcessTransport
from app.main import app
from app.worker import register_handlers

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
OLD_NAME = re.compile(r"customer", re.IGNORECASE)
OLD_EVENT_NAME = re.compile(r"customer\.[a-z_]+(\.[a-z_]+)*")

# (enum schema, value) → why the value may say «customer».
ALLOWED_ENUM_VALUES = {
    ("ContactRole", "customer"): "the role «Kunde» keeps its name (ADR-210)",
    ("NewPriceSource", "customer"): "a Neupreis the buyer supplied (FR-C-02), not the register",
    ("StandardTextAppliesTo", "customer_note"): "a stored standard-text type code, not the register",
}

# (event type, consumer name) — the old-name subscriptions kept until PR-5.
OLD_NAME_CONSUMERS = frozenset(
    {
        ("customer.merged", "sales.customer_merged"),
        ("customer.merged", "inventory.customer_merged"),
    }
)

# (file relative to app/, value) — `customer.*` string constants that may stay.
ALLOWED_OLD_EVENT_LITERALS = frozenset(
    {
        ("worker.py", "customer.merged"),  # the two subscriptions above
        *(("contact/reconciliation.py", old) for old in _BEFORE_THE_RENAME.values()),
        # Anto kept the placeholder's name (KAN-464 PR-3).
        ("platform/documents/services/standard_text_placeholders.py", "customer.salutation"),
    }
)


class _RecordingTransport(InProcessTransport):
    def __init__(self) -> None:
        super().__init__(session_factory=None)  # type: ignore[arg-type]
        self.registrations: list[tuple[str, str, Handler]] = []

    def register(self, event_type: str, *, consumer_name: str, handler: Handler) -> None:
        super().register(event_type, consumer_name=consumer_name, handler=handler)
        self.registrations.append((event_type, consumer_name, handler))


def _registrations() -> list[tuple[str, str, Handler]]:
    transport = _RecordingTransport()
    register_handlers(transport)
    return transport.registrations


def _names(schema: dict[str, Any]) -> Iterator[str]:
    """Every schema and field name of the OpenAPI document."""
    for name, component in schema.get("components", {}).get("schemas", {}).items():
        yield f"schema {name}"
        for field in component.get("properties", {}):
            yield f"field {name}.{field}"


def test_no_api_path_parameter_schema_or_field_says_customer() -> None:
    schema = app.openapi()
    hits = [f"path {path}" for path in schema["paths"] if OLD_NAME.search(path)]
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []) if isinstance(operation, dict) else []:
                if OLD_NAME.search(parameter["name"]):
                    hits.append(f"parameter {parameter['name']} of {method.upper()} {path}")
    hits += [name for name in _names(schema) if OLD_NAME.search(name)]
    assert hits == [], "KAN-464: the register is «contact» in the API (ADR-210)"


def test_enum_values_say_customer_only_where_listed() -> None:
    found = {
        (name, value)
        for name, component in app.openapi().get("components", {}).get("schemas", {}).items()
        for value in component.get("enum", [])
        if isinstance(value, str) and OLD_NAME.search(value)
    }
    assert found == set(ALLOWED_ENUM_VALUES), (
        "An enum value names «customer»: rename it, or list it with its reason. "
        "An entry that matches nothing any more is removed."
    )


def _old_event_literals() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in APP_ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and OLD_EVENT_NAME.fullmatch(node.value):
                found.add((path.relative_to(APP_ROOT).as_posix(), node.value))
    return found


def test_no_code_names_an_old_event_outside_the_list() -> None:
    assert _old_event_literals() == ALLOWED_OLD_EVENT_LITERALS, (
        "A `customer.*` event name in app/: events are `contact.*` since KAN-464 PR-3. "
        "Publish and subscribe the new name; an entry that matches nothing any more is removed."
    )


def test_old_name_subscriptions_are_exactly_the_listed_ones() -> None:
    old = {
        (event_type, consumer)
        for event_type, consumer, _ in _registrations()
        if OLD_NAME.search(event_type) or OLD_NAME.search(consumer)
    }
    assert old == OLD_NAME_CONSUMERS


def test_every_old_name_consumer_has_its_twin_on_the_new_name() -> None:
    """The contract: whoever consumed `customer.X` consumes `contact.X` with the same handler,
    so no consumer misses an event published under the new name."""

    new_for_old = {old: new for new, old in _BEFORE_THE_RENAME.items()}
    registrations = _registrations()
    on_new = {(event_type, handler) for event_type, _, handler in registrations}
    missing = [
        f"{consumer} on {event_type} has no consumer on {new_for_old.get(event_type)}"
        for event_type, consumer, handler in registrations
        if event_type.startswith("customer.") and (new_for_old.get(event_type), handler) not in on_new
    ]
    assert missing == []


def test_every_contact_event_is_in_the_old_name_map() -> None:
    """Each event the contact context publishes is a `contact.*` name the reconciliation knows
    the old name of, so its check covers every old-name row still pending or dead."""

    published = {
        value
        for path in (APP_ROOT / "contact").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.keyword)
        and node.arg == "event_type"
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for value in [node.value.value]
    }
    assert published, "the scan found no event the contact context publishes"
    assert published <= set(_BEFORE_THE_RENAME), sorted(published - set(_BEFORE_THE_RENAME))
    assert all(name.startswith("contact.") for name in published)


def test_the_guard_sees_an_old_name() -> None:
    assert OLD_EVENT_NAME.fullmatch("customer.created")
    assert OLD_EVENT_NAME.fullmatch("customer.vehicle_party.linked")
    assert not OLD_EVENT_NAME.fullmatch("contact.created")
    assert not OLD_EVENT_NAME.fullmatch("the customer.created event")
    assert OLD_NAME.search("/v1/customers/{customer_id}")

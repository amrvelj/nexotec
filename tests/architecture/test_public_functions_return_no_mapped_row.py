"""Architecture test (KAN-123 step 8): no function a context's `public` module
binds hands out a mapped row.

`test_public_exports_no_mapped_class.py` refuses the class itself; this one
refuses the row, which is the same door by another way: a public read that
answers a `Dealership` lets the caller change and flush platform's row past
the import-linter. Every function a `public` module binds must say what it
returns, and that annotation — through `X | None`, `list[X]`, `dict[K, X]`
and the fields of a dataclass or Pydantic model it names — names no mapped
class. Platform's seven reads were the last that did (`get_dealership_or_404`,
`get_location_or_404`, `get_dealer_group_or_404`, `enable_group_read`,
`get_active_user_acting_in`, `list_active_users_acting_in`, `notify`).

`_DEBT` holds what was found when this guard came, each with its ticket; it
only shrinks (a stale entry fails).
"""

import dataclasses
import importlib
import inspect as pyinspect
import types
import typing
import uuid
from pathlib import Path

import pydantic
from sqlalchemy import inspect
from sqlalchemy.orm import Mapper, Session

from app.platform.organisation.models.dealership import Dealership

_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_CONTEXTS = (
    "platform",
    "contact",
    "vehicle",
    "sales",
    "inventory",
    "valuation",
    "integration",
    "aftersales",
    "parts",
    "finance",
    "reporting",
    "compliance",
)

# Shrink-only. Empty since KAN-754 (`match_vehicle` answers a frozen `VehicleMatch`).
_DEBT: set[str] = set()


def _is_mapped_class(value: object) -> bool:
    return isinstance(value, type) and isinstance(inspect(value, raiseerr=False), Mapper)


def _mapped_in(annotation: object, seen: set[int] | None = None) -> set[str]:
    """Every mapped class an annotation names, through generics and the fields of the value types it names."""

    seen = set() if seen is None else seen
    if id(annotation) in seen:
        return set()
    seen.add(id(annotation))
    if _is_mapped_class(annotation):
        return {annotation.__name__}  # type: ignore[union-attr]
    found: set[str] = set()
    for arg in typing.get_args(annotation):
        found |= _mapped_in(arg, seen)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        for field_type in typing.get_type_hints(annotation).values():
            found |= _mapped_in(field_type, seen)
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        for field in annotation.model_fields.values():
            found |= _mapped_in(field.annotation, seen)
    return found


def _problems(module: types.ModuleType) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in vars(module).items():
        if name.startswith("_") or not pyinspect.isfunction(value):
            continue
        key = f"{module.__name__}.{name}"
        hints = typing.get_type_hints(value)
        if "return" not in hints:
            out[key] = "has no return annotation"
            continue
        mapped = _mapped_in(hints["return"])
        if mapped:
            out[key] = f"returns the mapped row {sorted(mapped)}"
    return out


def _public_modules() -> list[types.ModuleType]:
    return [
        importlib.import_module(f"app.{context}.public")
        for context in _CONTEXTS
        if (_APP_ROOT / context / "public.py").is_file()
    ]


def _all_problems(modules: list[types.ModuleType]) -> dict[str, str]:
    found: dict[str, str] = {}
    for module in modules:
        found |= _problems(module)
    return found


def test_no_public_function_hands_out_a_mapped_row() -> None:
    problems = {key: why for key, why in _all_problems(_public_modules()).items() if key not in _DEBT}
    assert problems == {}, (
        "A public function hands out (or may hand out) another context's live row. Return a frozen "
        f"value — a dataclass or Pydantic model of plain fields — instead (KAN-123, ADR-162): {problems}"
    )


def test_the_debt_list_only_shrinks() -> None:
    stale = _DEBT - set(_all_problems(_public_modules()))
    assert stale == set(), f"No longer a problem — delete from _DEBT: {sorted(stale)}"


def test_platform_reads_hand_out_values() -> None:
    platform = importlib.import_module("app.platform.public")
    assert {key for key in _problems(platform)} == set()


def test_self_test_a_row_returned_directly_nested_or_unannotated_is_reported() -> None:
    # Red before green: each shape that hands out a row is reported, a plain value is not.
    @dataclasses.dataclass(frozen=True)
    class Holder:
        row: Dealership | None

    class Read(pydantic.BaseModel):
        rows: list[Dealership]
        model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    @dataclasses.dataclass(frozen=True)
    class Plain:
        id: uuid.UUID

    def direct(db: Session) -> Dealership: ...
    def optional(db: Session) -> Dealership | None: ...
    def listed(db: Session) -> list[Dealership]: ...
    def held(db: Session) -> Holder: ...
    def modelled(db: Session) -> Read: ...
    def unannotated(db: Session): ...
    def plain(db: Session) -> Plain | None: ...

    fake = types.ModuleType("app.fake.public")
    for function in (direct, optional, listed, held, modelled, unannotated, plain):
        setattr(fake, function.__name__, function)

    found = _problems(fake)
    assert set(found) == {f"app.fake.public.{name}" for name in ("direct", "optional", "listed", "held", "modelled", "unannotated")}
    assert found["app.fake.public.unannotated"] == "has no return annotation"
    assert found["app.fake.public.held"] == "returns the mapped row ['Dealership']"

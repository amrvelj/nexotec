"""KAN-769 (ADR-190): a list that orders by a fixed `order_by` on a word column sorts it under the
text sort collation, through `app.core.text_collation.TextSortKey`, exactly as a `SortField` does —
or the list shows another order than the grids beside it (byte order on `C.UTF-8`: «Zeta» before
«alpha», «Ärztin» after «Z»).

The scan reads every `.order_by(...)` call in `app/` and resolves each argument written as
`Model.attribute` (or `Model.attribute.asc()` / `.desc()`) against the module's own names; one that
is a plain word column (`is_plain_text_column`) and not wrapped in `TextSortKey` fails, unless it is
a code listed below. An expression built any other way (a label, an alias, a `func` call) is not
resolved — `SortField` and `TextSortKey` are the two ways a word column is meant to be sorted.

A column listed here holds a code, a key or an identifier, which keeps byte order (ADR-190); a new
entry says why. A Python sort of words uses `sort_by_text`, never a `key=str.lower`
(`test_no_python_sort_of_words_by_lower`)."""

import ast
import importlib
from pathlib import Path

from app.core.text_collation import is_plain_text_column

_APP = Path(__file__).resolve().parents[2] / "app"

# (table, column): codes and keys, ordered in byte order on purpose.
_CODE_COLUMNS = {
    ("integration_ai_prompt", "code"): "a prompt's code",
    ("integration_provider_caption", "kind"): "a caption's kind code",
    ("integration_provider_caption", "code"): "a caption's code",
    ("standard_text", "code"): "a standard text's code",
    ("user_preference", "scope"): "a preference scope key",
    ("reference_value", "value_code"): "a reference value's code",
    ("holiday_calendar", "code"): "a calendar's code",
    ("location", "code"): "a location's code, ordering the number ranges",
    ("ui_translation", "namespace"): "a translation namespace key",
    ("ui_translation", "key"): "a translation key",
    ("missing_translation", "key"): "a translation key",
    ("vehicle_provider_code_map", "provider_code"): "a provider's code",
    ("vehicle_model_variant_energy_label", "gearbox"): "a gearbox code",
    ("vehicle_plate_lookup_cache", "stammnummer"): "a Stammnummer",
}


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(_APP.parent).with_suffix("").parts)


def _ordered_attribute(argument: ast.expr) -> ast.Attribute | None:
    if (
        isinstance(argument, ast.Call)
        and isinstance(argument.func, ast.Attribute)
        and argument.func.attr in {"asc", "desc", "nulls_first", "nulls_last"}
    ):
        argument = argument.func.value
    if isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name):
        return argument
    return None


def _plain_word_sorts() -> list[tuple[str, tuple[str, str]]]:
    found = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "order_by"
        ]
        if not calls:
            continue
        module = importlib.import_module(_module_name(path))
        for call in calls:
            for argument in call.args:
                attribute = _ordered_attribute(argument)
                if attribute is None:
                    continue
                owner = getattr(module, attribute.value.id, None)  # type: ignore[union-attr]
                column = getattr(owner, attribute.attr, None) if owner is not None else None
                if column is None or not is_plain_text_column(column):
                    continue
                table_column = column.property.columns[0]
                found.append(
                    (f"{path.relative_to(_APP.parent)}:{call.lineno}", (table_column.table.name, table_column.name))
                )
    return found


def test_the_scan_resolves_the_sorts_it_guards():
    """The scan sees through `Model.attribute` and `.asc()`/`.desc()`: the codes listed here are found."""

    found = {column for _, column in _plain_word_sorts()}
    for expected in (("ui_translation", "key"), ("standard_text", "code"), ("location", "code")):
        assert expected in found, expected


def test_every_fixed_sort_on_a_word_column_is_under_the_collation():
    offending = [
        f"{where}: {table}.{column}"
        for where, (table, column) in _plain_word_sorts()
        if (table, column) not in _CODE_COLUMNS
    ]
    assert not offending, (
        "A fixed order_by on a word column must sort under the collation — `TextSortKey(column)` "
        "(KAN-769, ADR-190); a code column is listed in _CODE_COLUMNS with why:\n" + "\n".join(offending)
    )


def test_every_listed_code_column_is_still_sorted_somewhere():
    """The list only shrinks with the code: an entry no sort uses any more goes."""

    found = {column for _, column in _plain_word_sorts()}
    assert not set(_CODE_COLUMNS) - found, sorted(set(_CODE_COLUMNS) - found)


def test_no_python_sort_of_words_by_lower():
    """`.lower()` is not the collation («Š…» after «Z»): a Python list of words is ordered with
    `app.core.text_collation.sort_by_text`."""

    offending = []
    for path in sorted(_APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in {"sorted", "sort"}):
                continue
            for keyword in node.keywords:
                if keyword.arg != "key":
                    continue
                lowered = any(
                    isinstance(inner, ast.Attribute) and inner.attr in {"lower", "casefold"}
                    for inner in ast.walk(keyword.value)
                )
                if lowered:
                    offending.append(f"{path.relative_to(_APP.parent)}:{node.lineno}")
    assert not offending, "Sort words with sort_by_text, not a lower-cased key (KAN-769):\n" + "\n".join(offending)

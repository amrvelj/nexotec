"""KAN-769 (ADR-190): a list that orders by a fixed `order_by` on a word column sorts it under the
text sort collation, through `app.core.text_collation.TextSortKey`, exactly as a `SortField` does —
or the list shows another order than the grids beside it (byte order on `C.UTF-8`: «Zeta» before
«alpha», «Ärztin» after «Z»).

The scan reads every `.order_by(...)` call in `app/` and resolves each argument written as
`Model.attribute` (or `Model.attribute.asc()` / `.desc()`) against the module's own names; one that
is a plain word column (`is_plain_text_column`) and not wrapped in `TextSortKey` fails, unless it is
a code listed below. An expression built any other way (a label, an alias, a `func` call) is not
resolved — `SortField` and `TextSortKey` are the two ways a word column is meant to be sorted.

A column that holds a code, a key or an identifier keeps byte order (ADR-190): it is declared
`info=IDENTIFIER_TEXT` on its model, the one declaration `SortField` reads too, so a fixed sort and
a grid never order the same column two ways. A Python sort of words uses `sort_by_text`, never a `key=str.lower`
(`test_no_python_sort_of_words_by_lower`)."""

import ast
import importlib
from pathlib import Path

from app.core.text_collation import is_identifier_text, is_plain_text_column

_APP = Path(__file__).resolve().parents[2] / "app"

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


def _text_sorts() -> list[tuple[str, tuple[str, str], bool]]:
    """(where, (table, column), is a word column) for every resolved fixed sort on a text column."""

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
                if column is None or not (is_plain_text_column(column) or is_identifier_text(column)):
                    continue
                table_column = column.property.columns[0]
                found.append(
                    (
                        f"{path.relative_to(_APP.parent)}:{call.lineno}",
                        (table_column.table.name, table_column.name),
                        is_plain_text_column(column),
                    )
                )
    return found


def test_the_scan_resolves_the_sorts_it_guards():
    """The scan sees through `Model.attribute` and `.asc()`/`.desc()`: the code columns sorted in byte
    order on purpose are found, and found as identifiers."""

    found = {column: word for _, column, word in _text_sorts()}
    for expected in (("ui_translation", "key"), ("standard_text", "code"), ("location", "code")):
        assert found.get(expected) is False, expected


def test_every_fixed_sort_on_a_word_column_is_under_the_collation():
    offending = [f"{where}: {table}.{column}" for where, (table, column), word in _text_sorts() if word]
    assert not offending, (
        "A fixed order_by on a word column must sort under the collation — `TextSortKey(column)` "
        "(KAN-769, ADR-190); a column that holds a code is declared `info=IDENTIFIER_TEXT`:\n" + "\n".join(offending)
    )


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

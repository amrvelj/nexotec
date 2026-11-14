"""KAN-182: every sorted list decodes its cursor against the sort it pages by.

`app.core.pagination.decode_sort_cursor` (a preserved file) checks a cursor's
shape only; a cursor minted under one `?sort=` and replayed under another
reached SQL with values of the wrong count or type — a 500. The one door is
`app.core.sorting.decode_cursor_for_sort(cursor, sort_fields)`, which also
checks the values against the sort fields and answers 400 `cursor_invalid`.
So in `app/` only `app/core/sorting.py` may name `decode_sort_cursor` (and
`app/core/pagination.py`, which defines it). A new sorted list calls
`decode_cursor_for_sort`; there is no allow-list.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"
_ALLOWED = {_APP / "core" / "pagination.py", _APP / "core" / "sorting.py"}
_NAME = "decode_sort_cursor"


def _names_it(tree: ast.AST) -> list[int]:
    def names_it(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == _NAME
        if isinstance(node, ast.Attribute):
            return node.attr == _NAME
        if isinstance(node, ast.ImportFrom):
            return any(alias.name == _NAME for alias in node.names)
        return False

    return [node.lineno for node in ast.walk(tree) if names_it(node)]


def test_only_sorting_decodes_a_sorted_cursor():
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        if path in _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{path.relative_to(_REPO_ROOT)}:{line}" for line in _names_it(tree)]
    assert offenders == [], (
        "Decode a sorted cursor with app.core.sorting.decode_cursor_for_sort(cursor, sort_fields), "
        f"never decode_sort_cursor directly (KAN-182): {offenders}"
    )


def test_the_guard_sees_an_import_and_a_call():
    """The guard is not vacuous: it finds both shapes a caller would use."""

    assert _names_it(ast.parse("from app.core.pagination import decode_sort_cursor\n")) == [1]
    assert _names_it(ast.parse("import app.core.pagination as p\np.decode_sort_cursor('x')\n")) == [2]

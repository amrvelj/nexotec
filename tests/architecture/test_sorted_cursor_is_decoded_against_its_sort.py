"""KAN-182: every sorted list decodes its cursor against the sort it pages by.

`app.core.pagination.decode_sort_cursor` (a preserved file) checks a cursor's
shape only; a cursor minted under one `?sort=` and replayed under another
reached SQL with values of the wrong count or type — a 500. The one door is
`app.core.sorting.decode_cursor_for_sort(cursor, sort_fields)`, which also
checks the values against the sort fields and answers 400 `cursor_invalid`.
So in `app/` only `app/core/sorting.py` may name `decode_sort_cursor` (and
`app/core/pagination.py`, which defines it). A new sorted list calls
`decode_cursor_for_sort`; there is no allow-list.

KAN-713: the same holds on the minting side. The preserved serialiser writes
no `date` and no `Decimal`, so `pagination.build_sorted_page` raised on a sort
by a Date or Numeric column — page two was a 500. The one door is
`app.core.sorting.build_page_for_sort(rows, params)`, which carries both on
tags the serialiser already has (ADR-179, KAN-713): only `app/core/sorting.py` may name
`build_sorted_page` or `encode_sort_cursor`.
"""

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP = _REPO_ROOT / "app"
_ALLOWED = {_APP / "core" / "pagination.py", _APP / "core" / "sorting.py"}
_NAME = "decode_sort_cursor"
_MINTING_NAMES = ("build_sorted_page", "encode_sort_cursor")


def _names_it(tree: ast.AST, name: str = _NAME) -> list[int]:
    def names_it(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id == name
        if isinstance(node, ast.Attribute):
            return node.attr == name
        if isinstance(node, ast.ImportFrom):
            return any(alias.name == name for alias in node.names)
        return False

    return [node.lineno for node in ast.walk(tree) if names_it(node)]


def _offenders(*names: str) -> list[str]:
    offenders = []
    for path in sorted(_APP.rglob("*.py")):
        if path in _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{path.relative_to(_REPO_ROOT)}:{line}" for name in names for line in _names_it(tree, name)]
    return offenders


def test_only_sorting_decodes_a_sorted_cursor():
    offenders = _offenders(_NAME)
    assert offenders == [], (
        "Decode a sorted cursor with app.core.sorting.decode_cursor_for_sort(cursor, sort_fields), "
        f"never decode_sort_cursor directly (KAN-182): {offenders}"
    )


def test_the_guard_sees_an_import_and_a_call():
    """The guard is not vacuous: it finds both shapes a caller would use."""

    assert _names_it(ast.parse("from app.core.pagination import decode_sort_cursor\n")) == [1]
    assert _names_it(ast.parse("import app.core.pagination as p\np.decode_sort_cursor('x')\n")) == [2]


def test_only_sorting_mints_a_sorted_cursor():
    offenders = _offenders(*_MINTING_NAMES)
    assert offenders == [], (
        "Mint a sorted list's next cursor with app.core.sorting.build_page_for_sort(rows, params), "
        f"never build_sorted_page or encode_sort_cursor directly (KAN-713): {offenders}"
    )


def test_the_minting_guard_sees_an_import_and_a_call():
    for name in _MINTING_NAMES:
        assert _names_it(ast.parse(f"from app.core.pagination import {name}\n"), name) == [1]
        assert _names_it(ast.parse(f"import app.core.pagination as p\np.{name}([], None)\n"), name) == [2]

"""Architecture test (KAN-805, ADR-214): every setting key read is declared,
and every declared key is read.

Keys are strings (`<area>.<name>`), so nothing in the type system ties a
read to its declaration. This test does, by reading the source:

- **read**: every call of `read_setting` under `app/` (the store's own module
  aside), which must name its key as a string literal, `key="area.name"`. A
  computed key, `read_setting` passed around, imported under another name or
  named in a string (a `getattr`), fails here with its place: a read this
  test cannot see is a read nobody can check.
- **declared**: what the composition root `app/setting_registry.py`
  registers at start-up (the test-only `testarea.*` samples aside).

It fails on a key read and not declared, and on a key declared and read by
nobody (a declaration ships with its reader, in the same ticket).

Not counted (stated, not hidden): a read in `tests/`; the administration's
listing of every declaration (it shows them, it does not use one). A read in
code that never runs still counts as a read.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"
_FUNCTION = "read_setting"
# The store defines it; platform.public re-exports it.
_DEFINED_IN = {
    _APP_ROOT / "platform" / "organisation" / "services" / "setting_store.py",
    _APP_ROOT / "platform" / "public.py",
}


def scan(sources: dict[str, str]) -> tuple[dict[str, list[str]], list[str]]:
    """({key: [where it is read]}, [places that use read_setting in a way the scan cannot pair])."""

    reads: dict[str, list[str]] = {}
    problems: list[str] = []
    for name, source in sorted(sources.items()):
        tree = ast.parse(source, filename=name)
        called: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if callee != _FUNCTION:
                continue
            called.add(id(func))
            key = next((kw.value for kw in node.keywords if kw.arg == "key"), None)
            where = f"{name}:{node.lineno}"
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                reads.setdefault(key.value, []).append(where)
            else:
                problems.append(f"{where}: {_FUNCTION} without a literal key=")
        for node in ast.walk(tree):
            where = f"{name}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Name) and node.id == _FUNCTION and id(node) not in called or isinstance(node, ast.Attribute) and node.attr == _FUNCTION and id(node) not in called:
                problems.append(f"{where}: {_FUNCTION} used other than called")
            elif isinstance(node, ast.alias) and node.name == _FUNCTION and node.asname not in (None, _FUNCTION):
                problems.append(f"{where}: {_FUNCTION} imported as {node.asname}")
            elif isinstance(node, ast.Constant) and node.value == _FUNCTION:
                problems.append(f"{where}: {_FUNCTION} named in a string")
    return reads, problems


def _app_sources() -> dict[str, str]:
    return {
        str(path.relative_to(_REPO_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(_APP_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts and path not in _DEFINED_IN
    }


def _declared_keys() -> set[str]:
    import app.setting_registry  # noqa: F401 — registers every area's declarations
    from app.platform.organisation.services.setting_declarations import declared_settings

    return {d.key for d in declared_settings() if not d.key.startswith("testarea.")}


def test_every_read_key_is_declared_and_every_declared_key_is_read():
    reads, problems = scan(_app_sources())
    declared = _declared_keys()

    assert not problems, "A setting read the scan cannot pair with its declaration:\n" + "\n".join(problems)
    undeclared = {key: where for key, where in reads.items() if key not in declared}
    assert not undeclared, (
        "Settings read but declared by no area (declare them in the area's registration, "
        f"called from app/setting_registry.py): {undeclared}"
    )
    unread = sorted(declared - set(reads))
    assert not unread, f"Settings declared but read by nobody (ship the reader with the declaration): {unread}"


# --- the scan on the shapes it must judge ------------------------------------------


def test_the_scan_pairs_a_literal_read_and_refuses_every_other_form():
    reads, problems = scan(
        {
            "a.py": (
                "from app.platform.public import read_setting\n"
                "def f(db):\n"
                "    read_setting(db, dealership_id=1, location_id=None, key='aftersales.time_unit')\n"
            ),
            "b.py": (
                "from app.platform import public\n"
                "def g(db, k):\n"
                "    public.read_setting(db, dealership_id=1, location_id=None, key='aftersales.time_unit')\n"
                "    public.read_setting(db, dealership_id=1, location_id=None, key=k)\n"
                "    reader = public.read_setting\n"
            ),
            "c.py": "from app.platform.public import read_setting as rs\nx = getattr(object, 'read_setting')\n",
        }
    )

    assert reads == {"aftersales.time_unit": ["a.py:3", "b.py:3"]}
    assert problems == [
        "b.py:4: read_setting without a literal key=",
        "b.py:5: read_setting used other than called",
        "c.py:1: read_setting imported as rs",
        "c.py:2: read_setting named in a string",
    ]


def test_an_undeclared_read_and_an_unread_declaration_would_fail(monkeypatch):
    """The main test, red on a source reading a key nobody declared, and on a
    declaration nobody reads."""

    import app.platform.organisation.services.setting_declarations as declarations
    from tests import setting_store_support as s

    source = {"x.py": "def f(db):\n    read_setting(db, dealership_id=1, location_id=None, key='aftersales.ghost')\n"}
    monkeypatch.setattr(sys.modules[__name__], "_app_sources", lambda: source)
    try:
        test_every_read_key_is_declared_and_every_declared_key_is_read()
    except AssertionError as failure:
        assert "aftersales.ghost" in str(failure)
    else:
        raise AssertionError("an undeclared read passed")

    unread = declarations.SettingDeclaration(
        key="planning.grid_minutes", module=None, kind=s.WAIT.kind, when_not_set=s.WAIT.when_not_set
    )
    monkeypatch.setattr(sys.modules[__name__], "_app_sources", dict)
    with s.declared(unread):
        try:
            test_every_read_key_is_declared_and_every_declared_key_is_read()
        except AssertionError as failure:
            assert "planning.grid_minutes" in str(failure)
        else:
            raise AssertionError("an unread declaration passed")

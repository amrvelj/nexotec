"""KAN-198: every production minting path passes the dealership's real
entitlements to create_access_token.

`create_access_token(entitlements=None)` means "every module" — a convenience
for tokens minted without a dealership behind them (tests, bootstrap). In
`app/` that default must never be relied on: a token minted without the
dealership's set would license every module. Scans every call in app/ for an
explicit `entitlements=` keyword.
"""

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent.parent / "app"


def _calls_missing_entitlements() -> list[str]:
    missing = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "create_access_token":
                continue
            if path.name == "auth.py" and path.parent.name == "core":
                continue  # the definition's own module has no calls; guard anyway
            keyword = next((kw for kw in node.keywords if kw.arg == "entitlements"), None)
            # A literal `entitlements=None` is the default spelled out — the same
            # "every module" fail-open the scan exists to keep out of app/.
            if keyword is None or (isinstance(keyword.value, ast.Constant) and keyword.value.value is None):
                missing.append(f"{path.relative_to(_APP.parent)}:{node.lineno}")
    return missing


def test_the_scan_finds_the_session_minting_call():
    calls = [
        f"{p.relative_to(_APP.parent)}"
        for p in _APP.rglob("*.py")
        if "create_access_token(" in p.read_text() and not (p.name == "auth.py" and p.parent.name == "core")
    ]
    assert "app/platform/api/auth.py" in calls, calls


def test_every_production_minting_call_passes_entitlements_explicitly():
    assert _calls_missing_entitlements() == []

"""Architecture test (KAN-635 PR 1, ADR-163): platform's `reference` sub-package is
the lowest of the four (reference ← organisation ← identity ← documents). Its
modules import nothing else of platform but `reference` itself and the frozen
`reference_data_seed` (frozen in place, counted as reference). An import of any
other `app.platform.*` module from `app/platform/reference/` fails by name.

There is no suppression mechanism: an import that reaches up means the module
belongs higher, or what it needs belongs lower — the cut changes, never this
test. KAN-635's last PR generalises this to every sub-package's direction.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REFERENCE = _REPO_ROOT / "app" / "platform" / "reference"
_ALLOWED_PREFIXES = ("app.platform.reference", "app.platform.reference_data_seed")


def _platform_imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return {name for name in found if name == "app.platform" or name.startswith("app.platform.")}


def _allowed(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _ALLOWED_PREFIXES)


def _upward_imports(sources: dict[str, str]) -> list[str]:
    """`path -> module` for every import, in `sources` (path -> text), that leaves `reference`."""

    return sorted(
        f"{path} -> {name}"
        for path, text in sources.items()
        for name in _platform_imports(text)
        if not _allowed(name)
        # `from app.platform.reference.services import mailer` also names the package itself.
        and not any(other.startswith(name + ".") and _allowed(other) for other in _platform_imports(text))
    )


def test_reference_imports_nothing_else_of_platform():
    sources = {
        str(path.relative_to(_REPO_ROOT)): path.read_text()
        for path in sorted(_REFERENCE.rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    assert sources, "app/platform/reference/ is empty: the guard would pass vacuously"

    assert _upward_imports(sources) == [], (
        "platform's reference sub-package is the lowest (ADR-163): it imports only itself and "
        "reference_data_seed. Move the module up, or what it needs down."
    )


def test_the_guard_reports_an_import_that_reaches_up():
    # Red before green on synthetic sources.
    assert _upward_imports({
        "app/platform/reference/services/a.py": "from app.platform.identity.services.user import get_user\n",
        "app/platform/reference/services/b.py": "import app.platform.organisation.models.dealership\n",
        "app/platform/reference/services/c.py": "from app.platform.public import notify\n",
        "app/platform/reference/services/ok.py": (
            "from app.platform.reference.services import mailer\n"
            "from app.platform.reference_data_seed import load_country_seed\n"
            "from app.core.audit import record_audit_event\n"
        ),
    }) == [
        "app/platform/reference/services/a.py -> app.platform.identity.services.user",
        "app/platform/reference/services/a.py -> app.platform.identity.services.user.get_user",
        "app/platform/reference/services/b.py -> app.platform.organisation.models.dealership",
        "app/platform/reference/services/c.py -> app.platform.public",
        "app/platform/reference/services/c.py -> app.platform.public.notify",
    ]

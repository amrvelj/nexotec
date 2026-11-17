"""Architecture test (KAN-635 criterion 4, ADR-163): platform's sub-packages import one way.

`platform` is one bounded context in five sub-packages, lowest first:

    reference ← organisation ← identity ← documents ← administration ← wiring

A module imports platform code from its own layer or a lower one only. The frozen seed
modules stay where the shipped migrations import them from (ADR-133): `reference_data_seed`
counts as reference, `services/standard_text_seed.py` as documents. The wiring is what
the context hands to the application: `public`, `consumers`, `daily_jobs`,
`reconciliation`, the health route and the package's `__init__`; it may import every layer.

Every module under `app/platform/` must belong to a layer: a new top-level module or
package that is not listed here fails by name, so nothing escapes the check by sitting
beside the sub-packages. There is no suppression mechanism: an import that reaches up means
the module belongs higher, or what it needs belongs lower — the cut changes, never this
test. This replaces the three interim guards of PR 1–3 (one per sub-package).
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLATFORM = _REPO_ROOT / "app" / "platform"

# Top-level name under app.platform -> layer (0 lowest). "" is the package itself.
_LAYERS: dict[str, int] = {
    "reference": 0,
    "reference_data_seed": 0,
    "organisation": 1,
    "identity": 2,
    "documents": 3,
    "services": 3,  # holds only the frozen standard_text_seed (documents, ADR-133)
    "administration": 4,
    "": 5,
    "public": 5,
    "consumers": 5,
    "daily_jobs": 5,
    "reconciliation": 5,
    "health": 5,
}
_NAMES = {0: "reference", 1: "organisation", 2: "identity", 3: "documents", 4: "administration", 5: "wiring"}


def _top(module: str) -> str | None:
    """The top-level name under `app.platform` (`""` for the package), None outside platform."""

    if module == "app.platform":
        return ""
    if module.startswith("app.platform."):
        return module.split(".")[2]
    return None


def _targets(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "app.platform":
                # `from app.platform import public` names the module, not the package.
                found.update(f"app.platform.{alias.name}" for alias in node.names)
            else:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return {name for name in found if _top(name) is not None}


def _module_top(path: str) -> str:
    parts = Path(path).with_suffix("").parts  # ("app", "platform", <top>, ...)
    return "" if len(parts) < 3 or parts[2] == "__init__" else parts[2]


def _violations(sources: dict[str, str]) -> list[str]:
    """Each unclassified module and each import, in `sources` (path -> text), that reaches up."""

    found: list[str] = []
    for path, text in sorted(sources.items()):
        own = _module_top(path)
        if own not in _LAYERS:
            found.append(f"{path}: app.platform.{own} belongs to no layer")
            continue
        for target in sorted(_targets(ast.parse(text))):
            top = _top(target)
            assert top is not None
            if top not in _LAYERS:
                found.append(f"{path} -> {target}: app.platform.{top} belongs to no layer")
            elif _LAYERS[top] > _LAYERS[own]:
                found.append(
                    f"{path} -> {target}: {_NAMES[_LAYERS[own]]} may not import {_NAMES[_LAYERS[top]]}"
                )
    return found


def _platform_sources() -> dict[str, str]:
    return {
        str(path.relative_to(_REPO_ROOT)): path.read_text()
        for path in sorted(_PLATFORM.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def test_platform_sub_packages_import_only_their_own_layer_or_lower():
    sources = _platform_sources()
    for top in ("reference", "organisation", "identity", "documents", "administration"):
        assert sum(path.startswith(f"app/platform/{top}/") for path in sources) > 5, (
            f"app/platform/{top}/ is (nearly) empty: the guard would pass vacuously"
        )

    assert _violations(sources) == [], (
        "platform's sub-packages import one way (ADR-163): reference ← organisation ← identity ← "
        "documents ← administration ← wiring. Move the module up, or what it needs down; a new "
        "top-level module is classified in _LAYERS."
    )


def test_every_top_level_name_of_platform_is_classified():
    on_disk = {
        path.stem if path.is_file() else path.name
        for path in _PLATFORM.iterdir()
        if path.name != "__pycache__" and (path.is_dir() or path.suffix == ".py")
    }
    assert on_disk - {"__init__"} <= set(_LAYERS), sorted(on_disk - set(_LAYERS) - {"__init__"})
    assert set(_LAYERS) - {""} <= on_disk, "a layer entry names nothing on disk: delete it"


def test_the_guard_reports_upward_and_unclassified_imports():
    # Red before green on synthetic sources.
    assert _violations({
        "app/platform/reference/services/a.py": "from app.platform.organisation.models.dealership import Dealership\n",
        "app/platform/organisation/services/b.py": "import app.platform.identity.models.user\n",
        "app/platform/identity/services/c.py": "from app.platform.documents.services import standard_text\n",
        "app/platform/documents/services/d.py": "from app.platform.administration.services import setup_checklist\n",
        "app/platform/administration/services/e.py": "from app.platform import public\n",
        "app/platform/administration/services/f.py": "from app.platform.public import notify\n",
        "app/platform/identity/services/g.py": "from app.platform.services.standard_text_seed import SEED_TEXTS\n",
        "app/platform/documents/services/h.py": "from app.platform.flat.x import y\n",
        "app/platform/stray.py": "x = 1\n",
        "app/platform/reference/services/ok.py": "from app.platform.reference_data_seed import load_country_seed\n",
        "app/platform/administration/services/ok.py": (
            "from app.platform.administration.models.export_job import ExportJob\n"
            "from app.platform.documents.services import document_render\n"
            "from app.platform.services.standard_text_seed import SEED_TEXTS\n"
            "from app.platform.identity.models.user import User\n"
            "from app.platform.organisation.models.dealership import Dealership\n"
            "from app.platform.reference.services import mailer\n"
            "from app.core.audit import record_audit_event\n"
        ),
        "app/platform/public.py": "from app.platform.administration.services import data_export\n",
    }) == [
        "app/platform/administration/services/e.py -> app.platform.public: administration may not import wiring",
        "app/platform/administration/services/f.py -> app.platform.public: administration may not import wiring",
        ("app/platform/documents/services/d.py -> app.platform.administration.services: "
        "documents may not import administration"),
        "app/platform/documents/services/h.py -> app.platform.flat.x: app.platform.flat belongs to no layer",
        "app/platform/identity/services/c.py -> app.platform.documents.services: identity may not import documents",
        ("app/platform/identity/services/g.py -> app.platform.services.standard_text_seed: "
        "identity may not import documents"),
        ("app/platform/organisation/services/b.py -> app.platform.identity.models.user: "
        "organisation may not import identity"),
        ("app/platform/reference/services/a.py -> app.platform.organisation.models.dealership: "
        "reference may not import organisation"),
        "app/platform/stray.py: app.platform.stray belongs to no layer",
    ]

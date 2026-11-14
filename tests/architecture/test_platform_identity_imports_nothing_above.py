"""Architecture test (KAN-635 PR 3, ADR-163): platform's `identity` sub-package sits
directly above `organisation` (reference ← organisation ← identity ← documents). Its
modules import nothing else of platform but `identity` itself, `organisation`, `reference`
and the frozen `reference_data_seed` (counted as reference). An import of any other
`app.platform.*` module — the flat `api/`, `models/`, `schemas/` and `services/` still
holding documents' modules and the ones not yet classified, `session_guard`, `public`,
`consumers`, … — from `app/platform/identity/` fails by name.

There is no suppression mechanism: an import that reaches up means the module belongs
higher, or what it needs belongs lower — the cut changes, never this test. KAN-635's last
PR generalises this guard and the reference and organisation ones to every sub-package's
direction.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IDENTITY = _REPO_ROOT / "app" / "platform" / "identity"
_ALLOWED_PREFIXES = (
    "app.platform.identity",
    "app.platform.organisation",
    "app.platform.reference",
    "app.platform.reference_data_seed",
)


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
    """`path -> module` for every import, in `sources` (path -> text), that leaves identity and what lies below it."""

    return sorted(
        f"{path} -> {name}"
        for path, text in sources.items()
        for name in _platform_imports(text)
        if not _allowed(name)
        # `from app.platform.identity.services import user` also names the package itself.
        and not any(other.startswith(name + ".") and _allowed(other) for other in _platform_imports(text))
    )


def test_identity_imports_only_itself_organisation_and_reference():
    sources = {
        str(path.relative_to(_REPO_ROOT)): path.read_text()
        for path in sorted(_IDENTITY.rglob("*.py"))
        if "__pycache__" not in path.parts
    }
    assert len(sources) > 50, "app/platform/identity/ is (nearly) empty: the guard would pass vacuously"

    assert _upward_imports(sources) == [], (
        "platform's identity sub-package sits above organisation and reference only (ADR-163): it "
        "imports itself, organisation, reference and reference_data_seed. Move the module up, or "
        "what it needs down."
    )


def test_the_guard_reports_an_import_that_reaches_up():
    # Red before green on synthetic sources.
    assert _upward_imports({
        "app/platform/identity/services/a.py": "from app.platform.services.standard_text import list_texts\n",
        "app/platform/identity/services/b.py": "import app.platform.models.document_template\n",
        "app/platform/identity/services/c.py": "from app.platform.public import notify\n",
        "app/platform/identity/services/d.py": "from app.platform.services import legal_documents\n",
        "app/platform/identity/services/e.py": "from app.platform import session_guard\n",
        "app/platform/identity/services/ok.py": (
            "from app.platform.identity.services import user\n"
            "from app.platform.identity.models.user import User\n"
            "from app.platform.organisation.models.dealership import Dealership\n"
            "from app.platform.reference.services import mailer\n"
            "from app.platform.reference_data_seed import load_country_seed\n"
            "from app.core.audit import record_audit_event\n"
        ),
    }) == [
        "app/platform/identity/services/a.py -> app.platform.services.standard_text",
        "app/platform/identity/services/a.py -> app.platform.services.standard_text.list_texts",
        "app/platform/identity/services/b.py -> app.platform.models.document_template",
        "app/platform/identity/services/c.py -> app.platform.public",
        "app/platform/identity/services/c.py -> app.platform.public.notify",
        "app/platform/identity/services/d.py -> app.platform.services",
        "app/platform/identity/services/d.py -> app.platform.services.legal_documents",
        "app/platform/identity/services/e.py -> app.platform",
        "app/platform/identity/services/e.py -> app.platform.session_guard",
    ]

"""Architecture test (KAN-202; Authentication & Identity FR-AU-10, D-A-16):
account recovery never sets or reads a password.

"Nobody, platform staff included, ever sets or reads a password" — D-A-16
lets a platform_admin recover an account alone, and FR-AU-10 holds without
exception: a re-issued invitation lets the *user* set one, a cleared
lockout touches the failure counter and the lock, and nothing else.

Two checks:

1. The recovery code — its routes, its service and its schemas — never
   names a password hash, the password history, or a function that writes
   or verifies a password (grep-based, like test_no_ambient_group_read).
   `identity.issue_invitation` mails a link; `set_password` is reachable
   only from the user's own acceptance or reset.
2. No request body under `/v1/platform/` has a field that could carry a
   password, and no response does either (the published OpenAPI). The one
   name allowed is `hasPassword`: a yes/no the recovery page shows (an
   invited user holds none yet), never the value.
"""

import re
from pathlib import Path

from app.main import app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_RECOVERY_CODE = (
    _APP_ROOT / "platform" / "api" / "recovery.py",
    _APP_ROOT / "platform" / "services" / "recovery.py",
    _APP_ROOT / "platform" / "schemas" / "recovery.py",
)

_FORBIDDEN = re.compile(
    r"password_hash|password_history|password_hashing|set_password|hash_password|verify_password|"
    r"validate_new_password|\bpassword\s*="
)

_ALLOWED = {"hasPassword"}


def test_the_recovery_code_never_names_a_password_write_or_read():
    offenders = []
    for path in _RECOVERY_CODE:
        assert path.exists(), f"{path} moved — update this test"
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if _FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "Recovery must never set or read a password (FR-AU-10, D-A-16):\n" + "\n".join(offenders)


def _schema_names(node, components, seen) -> set[str]:
    """Every property name reachable from an OpenAPI schema node."""

    names: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                names |= _schema_names(components.get(name, {}), components, seen)
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _schema_names(value, components, seen)
    elif isinstance(node, list):
        for item in node:
            names |= _schema_names(item, components, seen)
    return names


def test_no_platform_route_carries_a_password():
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    platform_paths = {path: ops for path, ops in spec["paths"].items() if path.startswith("/v1/platform/")}
    assert platform_paths, "the recovery door is gone — update this test"
    carrying = []
    for path, operations in platform_paths.items():
        for method, operation in operations.items():
            for name in _schema_names(operation, components, set()) - _ALLOWED:
                if "password" in name.lower() or "secret" in name.lower():
                    carrying.append(f"{method.upper()} {path}: {name}")
    assert carrying == [], "A platform route carries a password-shaped field:\n" + "\n".join(carrying)

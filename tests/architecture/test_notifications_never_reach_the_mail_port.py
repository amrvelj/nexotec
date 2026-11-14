"""Staff notifications are in-app and push only — never e-mail (KAN-216;
Dealer Administration D-A-40: "No staff e-mail notifications at all").

The notification service, its push port and consumers, its API and the
security family's wiring must not reach the platform mailer — not directly
and not through anything they import inside `app/`. Invitations and resets
are flows, not notifications, and keep their e-mail; they live outside these
modules.
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "app"

NOTIFICATION_MODULES = (
    "app.platform.services.notifications",
    "app.platform.services.notification_catalogue",
    "app.platform.services.push_subscriptions",
    "app.platform.reference.services.web_push",
    "app.platform.services.security_alerts",
    "app.platform.consumers",
    "app.platform.api.notifications",
    "app.platform.models.notification",
    "app.platform.schemas.notification",
)
MAIL_PORT = ("app.platform.reference.services.mailer", "app.platform.services.mail_texts", "smtplib", "email")


def _path_of(module: str) -> Path | None:
    base = _ROOT / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def _imports(module: str) -> set[str]:
    path = _path_of(module)
    if path is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _is_mail(name: str) -> bool:
    return any(name == port or name.startswith(port + ".") for port in MAIL_PORT)


def reach(start: str) -> dict[str, str]:
    """Every module reachable from `start` inside app/ (and the mail ports
    outside it), each with the module that imported it."""

    seen: dict[str, str] = {start: ""}
    queue = [start]
    while queue:
        current = queue.pop()
        for name in _imports(current):
            if name in seen:
                continue
            if _is_mail(name) or (name.startswith("app.") and _path_of(name) is not None):
                seen[name] = current
                if not _is_mail(name):
                    queue.append(name)
    return seen


def test_no_notification_module_reaches_the_mail_port():
    failures = []
    for module in NOTIFICATION_MODULES:
        assert _path_of(module) is not None, f"{module} moved — update this test"
        for name, via in reach(module).items():
            if _is_mail(name):
                failures.append(f"{module} reaches {name} (imported by {via})")
    assert not failures, "\n".join(failures)


def test_the_walk_would_see_a_mail_import():
    # The identity service sends the invitation and reset mails: the walk
    # must find the mailer from there, or it proves nothing above.
    assert any(_is_mail(name) for name in reach("app.platform.services.identity"))

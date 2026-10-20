"""Architecture test (KAN-201; Authentication & Identity FR-AU-23, D-A-15):
a connected account is a grant, never an identity.

A user's linked Microsoft 365 account (`user_connected_account`) is used to
act on the user's behalf in Outlook or a calendar. It never signs anyone in,
never resolves a `user_id` and never appears in a session token — "a linked
Microsoft account and a federated Microsoft login are two different things
even when it is the same Microsoft account". Only an `IdentityProvider`
(`EntraOidcProvider`, [P3]) may ever treat Microsoft as an identity, and it
does so through `dealership_auth_config`, not through a user's grant.

So two checks, by source (grep-based, like test_no_ambient_group_read):

1. The identity and session layer — token minting and the principal
   (`app/core/auth.py`), the identity providers, the session guard and the
   session, token and MFA services, and the sign-in routes — never names
   the model, its table or its service.
2. The model and its table are named only in an exact allowlist: the model,
   the one service that reads and writes it, and the registry. Everything
   else — the `/v1/me` routes, the manager's routes, the deactivation —
   goes through the service's functions, so a new reader is a deliberate
   entry here, never an accident.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "app"

_IDENTITY_AND_SESSION_LAYER = (
    _APP_ROOT / "core" / "auth.py",
    _APP_ROOT / "platform" / "services" / "identity.py",
    _APP_ROOT / "platform" / "services" / "auth_sessions.py",
    _APP_ROOT / "platform" / "services" / "auth_tokens.py",
    _APP_ROOT / "platform" / "services" / "mfa.py",
    _APP_ROOT / "platform" / "session_guard.py",
    _APP_ROOT / "platform" / "api" / "auth.py",
)

# Any mention of the model, its table, its module or its service.
_MENTION = re.compile(r"UserConnectedAccount|user_connected_account|connected_account\b|connected_account\.")

_MODEL_OR_TABLE = re.compile(r"\bUserConnectedAccount\b|\buser_connected_account\b")

_ALLOWED_MODEL_READERS = {
    _APP_ROOT / "platform" / "models" / "connected_account.py",
    _APP_ROOT / "platform" / "services" / "connected_account.py",
    # Registers every model on Base.metadata; reads nothing.
    _APP_ROOT / "model_registry.py",
    # Nightly reconciliation checks its actor columns against `user` (KAN-243);
    # it reads ids, never a grant.
    _APP_ROOT / "platform" / "reconciliation.py",
}


def test_the_identity_and_session_layer_never_reads_a_connected_account():
    offenders = []
    for path in _IDENTITY_AND_SESSION_LAYER:
        assert path.exists(), f"{path} moved — keep this list true"
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _MENTION.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "A connected account is a grant, never an identity (FR-AU-23): the sign-in and session layer must "
        "not read it.\n" + "\n".join(offenders)
    )


def test_the_connected_account_model_is_read_only_through_its_service():
    offenders = []
    for path in sorted(_APP_ROOT.rglob("*.py")):
        if path in _ALLOWED_MODEL_READERS:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if _MODEL_OR_TABLE.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "user_connected_account is read and written only by app/platform/services/connected_account.py; "
        "a new reader is an entry in this test's allowlist with its reason.\n" + "\n".join(offenders)
    )


def test_the_checks_see_a_violation():
    """The patterns match what a violation would look like (a red check)."""

    assert _MENTION.search("from app.platform.models.connected_account import UserConnectedAccount")
    assert _MENTION.search("from app.platform.services import connected_account")
    assert _MENTION.search("connected_account.list_for_user(db, user_id=user.id)")
    assert _MODEL_OR_TABLE.search("select(UserConnectedAccount).where(...)")
    assert _MODEL_OR_TABLE.search('text("SELECT * FROM user_connected_account")')
    assert not _MENTION.search('"connected_accounts": Capability(...)')
    assert not _MODEL_OR_TABLE.search("def list_user_connected_accounts(")

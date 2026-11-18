"""WP-2 PR-4 (closes G-10): Infisical-backed secret resolution.

No real Infisical project is reachable from here — every test either
proves the (much more common) not-configured no-op path directly, or
substitutes a fake client for the one real network-touching path so the
resolution logic itself is still genuinely exercised.
"""

import app.core.secrets as secrets_module
from app.core.secrets import resolve_secret_env


def test_resolve_is_a_noop_when_the_env_var_is_already_set(monkeypatch):
    """The plain env var always wins — no Infisical call is made at all,
    not even a configuration check, when it's already present.
    """

    monkeypatch.setenv("DMS_SOME_KEY", "already-here")
    monkeypatch.delenv("DMS_INFISICAL_PROJECT_ID", raising=False)
    resolve_secret_env("DMS_SOME_KEY", infisical_secret_name="SOME_KEY")
    assert __import__("os").environ["DMS_SOME_KEY"] == "already-here"


def test_resolve_is_a_noop_when_infisical_is_not_configured(monkeypatch):
    monkeypatch.delenv("DMS_UNSET_KEY", raising=False)
    monkeypatch.delenv("DMS_INFISICAL_PROJECT_ID", raising=False)
    monkeypatch.delenv("DMS_INFISICAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMS_INFISICAL_CLIENT_SECRET", raising=False)

    resolve_secret_env("DMS_UNSET_KEY", infisical_secret_name="UNSET_KEY")

    import os

    assert "DMS_UNSET_KEY" not in os.environ


def test_resolve_is_a_noop_when_only_some_infisical_vars_are_set(monkeypatch):
    """Partial configuration (e.g. a project ID with no credentials) is
    treated as not-configured, not as an error — same posture as every
    other optional feature in this codebase.
    """

    monkeypatch.delenv("DMS_UNSET_KEY", raising=False)
    monkeypatch.setenv("DMS_INFISICAL_PROJECT_ID", "proj-123")
    monkeypatch.delenv("DMS_INFISICAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("DMS_INFISICAL_CLIENT_SECRET", raising=False)

    resolve_secret_env("DMS_UNSET_KEY", infisical_secret_name="UNSET_KEY")

    import os

    assert "DMS_UNSET_KEY" not in os.environ


def test_resolve_fetches_from_infisical_and_sets_the_env_var(monkeypatch):
    """Substitutes a fake client for the real network call — proves
    resolve_secret_env() wires the right names through (secret_name,
    project_id, environment_slug, secret_path) and writes secretValue into
    the target env var, without touching a real Infisical project.
    """

    monkeypatch.delenv("DMS_FETCHED_KEY", raising=False)
    monkeypatch.setenv("DMS_INFISICAL_PROJECT_ID", "proj-123")
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_ID", "client-abc")
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_SECRET", "secret-xyz")

    calls = []

    class _FakeSecret:
        secretValue = "fetched-from-infisical"

    class _FakeSecrets:
        def get_secret_by_name(self, *, secret_name, project_id, environment_slug, secret_path):
            calls.append(
                {
                    "secret_name": secret_name,
                    "project_id": project_id,
                    "environment_slug": environment_slug,
                    "secret_path": secret_path,
                }
            )
            return _FakeSecret()

    class _FakeClient:
        secrets = _FakeSecrets()

    secrets_module._client.cache_clear()
    monkeypatch.setattr(secrets_module, "_client", lambda: _FakeClient())

    resolve_secret_env("DMS_FETCHED_KEY", infisical_secret_name="FETCHED_KEY")

    import os

    assert os.environ["DMS_FETCHED_KEY"] == "fetched-from-infisical"
    assert calls == [
        {
            "secret_name": "FETCHED_KEY",
            "project_id": "proj-123",
            "environment_slug": "prod",
            "secret_path": "/",
        }
    ]

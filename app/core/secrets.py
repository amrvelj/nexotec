"""Infisical-backed secret resolution (WP-2 PR-4, closes G-10).

Populates an environment variable from Infisical, in-process, before
Settings() reads it — never the other way around. This means every
existing consumer of Settings.tax_id_encryption_key / .jwt_private_key
(app/core/types.py::EncryptedString, app/core/auth.py) needs no changes at
all: those fields stay required strings with no default, exactly as
before this module existed, and still fail app startup the same way if
neither a plain env var nor Infisical produces a value.

Not configured (no DMS_INFISICAL_* vars) -> resolve_secret_env() is a
no-op and the plain env var each setting already reads is what Settings()
sees, same as every environment before this PR. Local dev and CI don't
need a live Infisical project to run — only a real deployment that
chooses to wire DMS_INFISICAL_* needs one, and even then only the machine
identity's client_id/client_secret ever sit in that deployment's plain
environment — never the tax_id key or the JWT private key themselves.

No secret value is ever logged here, by construction: this module doesn't
log at all. See app/core/observability.py's redaction note for where that
rule is enforced for everything else.
"""

import os
from functools import lru_cache

_ENV_PROJECT_ID = "DMS_INFISICAL_PROJECT_ID"
_ENV_CLIENT_ID = "DMS_INFISICAL_CLIENT_ID"
_ENV_CLIENT_SECRET = "DMS_INFISICAL_CLIENT_SECRET"
_ENV_HOST = "DMS_INFISICAL_HOST"
_ENV_ENVIRONMENT = "DMS_INFISICAL_ENVIRONMENT"
_ENV_SECRET_PATH = "DMS_INFISICAL_SECRET_PATH"


def _infisical_configured() -> bool:
    return bool(os.environ.get(_ENV_PROJECT_ID) and os.environ.get(_ENV_CLIENT_ID) and os.environ.get(_ENV_CLIENT_SECRET))


@lru_cache
def _client():
    # Imported lazily so environments that never configure Infisical (every
    # test run, most of local dev) don't pay for it and can't be broken by
    # a transitive dependency issue in a package they never exercise.
    from infisical_sdk import InfisicalSDKClient

    client = InfisicalSDKClient(host=os.environ.get(_ENV_HOST, "https://app.infisical.com"))
    client.auth.universal_auth.login(
        client_id=os.environ[_ENV_CLIENT_ID], client_secret=os.environ[_ENV_CLIENT_SECRET]
    )
    return client


def resolve_secret_env(env_var_name: str, *, infisical_secret_name: str) -> None:
    """If `env_var_name` is already set, do nothing — an operator who set
    the plain env var directly (every test run; local dev without
    Infisical configured) always wins, no Infisical call is made at all.
    Otherwise, when Infisical is configured, fetch `infisical_secret_name`
    and set `env_var_name` to it. Otherwise a no-op either way — the
    caller's own Settings() construction is what raises, the same
    "missing required field" error as before Infisical existed.
    """

    if os.environ.get(env_var_name):
        return
    if not _infisical_configured():
        return

    secret = _client().secrets.get_secret_by_name(
        secret_name=infisical_secret_name,
        project_id=os.environ[_ENV_PROJECT_ID],
        environment_slug=os.environ.get(_ENV_ENVIRONMENT, "prod"),
        secret_path=os.environ.get(_ENV_SECRET_PATH, "/"),
    )
    os.environ[env_var_name] = secret.secretValue

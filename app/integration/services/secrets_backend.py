"""Per-connection secret CRUD against Infisical (WP-6 PR-1).

Deliberately NOT a reuse of `app/core/secrets.py::resolve_secret_env` —
that module is read-once-at-startup for a small, fixed set of static
deploy-time secrets (tax_id key, JWT key, Zitadel secret, session key),
lazily caching one client for the process's whole lifetime. This module
does live, per-connection CRUD at request time: a dealer connecting a new
account creates a secret; rotating replaces one; disabling/deleting a
connection deletes them. Both modules read the exact same
`DMS_INFISICAL_*` environment configuration — there is only ever one
Infisical project — but this one never touches `app.core.secrets`'s
module-private `_client()`/`_infisical_configured()` (those are private
by convention, not a shared utility) and constructs its own client using
the identical `client.auth.universal_auth.login(...)` pattern.

`secret_ref` (what this module returns, and what the database ever
stores) is a PATH STRING — `secret_path`/`secret_name` — never a value.
`resolve_secret` is the one function that returns actual secret material,
and it is never exported from `app.integration.public`: only the
provider adapters (PR-3), which already live inside this context, may
call it. Everyone outside the context is structurally blocked from
`app.integration.services` by the reciprocal import-linter contract
regardless of what this module exports.

No secret value is ever logged here (this module doesn't log at all —
same posture as `app/core/secrets.py`'s own docstring), and the slot
field names are registered in `app/core/redact.py::SECRET_FIELDS` so an
accidental `extra={}` log elsewhere still redacts them.
"""

import os
import uuid
from functools import lru_cache

from app.core.errors import ConflictError

_ENV_PROJECT_ID = "DMS_INFISICAL_PROJECT_ID"
_ENV_CLIENT_ID = "DMS_INFISICAL_CLIENT_ID"
_ENV_CLIENT_SECRET = "DMS_INFISICAL_CLIENT_SECRET"
_ENV_HOST = "DMS_INFISICAL_HOST"
_ENV_ENVIRONMENT = "DMS_INFISICAL_ENVIRONMENT"

_SECRET_PATH_PREFIX = "/integrations"


class SecretsBackendNotConfigured(ConflictError):
    """Raised when a connection secret CRUD call is attempted but no
    Infisical project is configured — every test run and most of local
    dev. A `ConflictError` subclass (not a bare Exception) so it flows
    through the existing `app_error_handler` as a clean 409 with the
    standard error body, never a raw 500 that might carry the attempted
    value in a debug traceback.
    """

    def __init__(self) -> None:
        super().__init__("No Infisical project is configured for this deployment.")


def _configured() -> bool:
    return bool(
        os.environ.get(_ENV_PROJECT_ID) and os.environ.get(_ENV_CLIENT_ID) and os.environ.get(_ENV_CLIENT_SECRET)
    )


@lru_cache
def _client():
    from infisical_sdk import InfisicalSDKClient

    client = InfisicalSDKClient(host=os.environ.get(_ENV_HOST, "https://app.infisical.com"))
    client.auth.universal_auth.login(
        client_id=os.environ[_ENV_CLIENT_ID], client_secret=os.environ[_ENV_CLIENT_SECRET]
    )
    return client


def _secret_path(connection_id: uuid.UUID) -> str:
    return f"{_SECRET_PATH_PREFIX}/{connection_id}/"


def _environment_slug() -> str:
    return os.environ.get(_ENV_ENVIRONMENT, "prod")


def secret_ref_for(*, connection_id: uuid.UUID, slot: str) -> str:
    """The pointer this module's callers store in `integration_secret_ref.
    secret_ref` — a path string, computable without any Infisical call.
    """

    return f"{_secret_path(connection_id)}{slot}"


def create_secret(*, connection_id: uuid.UUID, slot: str, value: str) -> str:
    if not _configured():
        raise SecretsBackendNotConfigured()
    _client().secrets.create_secret_by_name(
        secret_name=slot,
        secret_path=_secret_path(connection_id),
        environment_slug=_environment_slug(),
        project_id=os.environ[_ENV_PROJECT_ID],
        secret_value=value,
    )
    return secret_ref_for(connection_id=connection_id, slot=slot)


def update_secret(*, connection_id: uuid.UUID, slot: str, value: str) -> str:
    """"Rotate" — replaces, never reads (Integrations & API Credentials
    v0.1, rule 2)."""

    if not _configured():
        raise SecretsBackendNotConfigured()
    _client().secrets.update_secret_by_name(
        current_secret_name=slot,
        secret_path=_secret_path(connection_id),
        environment_slug=_environment_slug(),
        project_id=os.environ[_ENV_PROJECT_ID],
        secret_value=value,
    )
    return secret_ref_for(connection_id=connection_id, slot=slot)


def delete_secret(*, connection_id: uuid.UUID, slot: str) -> None:
    if not _configured():
        raise SecretsBackendNotConfigured()
    _client().secrets.delete_secret_by_name(
        secret_name=slot,
        secret_path=_secret_path(connection_id),
        environment_slug=_environment_slug(),
        project_id=os.environ[_ENV_PROJECT_ID],
    )


def resolve_secret(*, connection_id: uuid.UUID, slot: str) -> str:
    """In-memory only, per call, by the owning gateway only (rule 3). Never
    persisted anywhere but the process's own call stack. Callers MUST be
    inside app.integration.adapters — see this module's own docstring.
    """

    if not _configured():
        raise SecretsBackendNotConfigured()
    secret = _client().secrets.get_secret_by_name(
        secret_name=slot,
        secret_path=_secret_path(connection_id),
        environment_slug=_environment_slug(),
        project_id=os.environ[_ENV_PROJECT_ID],
    )
    return secret.secretValue

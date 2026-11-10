"""Per-connection secret CRUD against Infisical (WP-6 PR-1).

Deliberately NOT a reuse of `app/core/secrets.py::resolve_secret_env` —
that module is read-once-at-startup for a small, fixed set of static
deploy-time secrets (tax_id key, JWT key, the platform mailbox password),
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

import logging
import os
import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar

from app.core.errors import ConflictError

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

_ENV_PROJECT_ID = "DMS_INFISICAL_PROJECT_ID"
_ENV_CLIENT_ID = "DMS_INFISICAL_CLIENT_ID"
_ENV_CLIENT_SECRET = "DMS_INFISICAL_CLIENT_SECRET"
_ENV_HOST = "DMS_INFISICAL_HOST"
_ENV_ENVIRONMENT = "DMS_INFISICAL_ENVIRONMENT"

_SECRET_PATH_PREFIX = "/integrations"
_ROOT_FOLDER = _SECRET_PATH_PREFIX.lstrip("/")


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


class SecretsBackendError(ConflictError):
    """The secrets manager refused or failed an operation (KAN-604) — a
    typed refusal through `app_error_handler`, never a raw 500.

    The message and `details` name the operation and the HTTP status the
    secrets manager answered, never the value, the secret's path or the
    SDK's own message (which can echo the path it was asked about). The SDK
    exception stays on the traceback through `raise ... from`.
    """

    def __init__(self, *, operation: str, status: int | None) -> None:
        answered = f"HTTP {status}" if status is not None else "no usable answer"
        super().__init__(
            f"The secrets manager refused '{operation}' ({answered}).",
            details={"reason": "secrets_backend_failed", "operation": operation, "status": status},
        )
        self.operation = operation
        self.status = status


def _status_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _call(operation: str, fn: Callable[[], _T]) -> _T:
    """Every Infisical SDK call goes through here, and whatever it raises
    becomes a `SecretsBackendError` naming `operation`:

    - `APIError` — Infisical answered with an HTTP error; `status` is it.
    - `InfisicalError` without a status — an answer that was not JSON.
    - `OSError` — no answer at all (refused connection, DNS, TLS, a
      timeout). The SDK sends its requests outside the `try` that makes its
      own errors, and retries a network error four times first, so these
      reach us as the `requests` library raised them; its
      `RequestException` is an `OSError` subclass, so `requests` is not
      imported here (KAN-550 keeps it out of `app/`).

    Each failure is logged at WARNING with the operation, the status and the
    exception's class — never its message, which can echo the secret's
    path — so a misconfigured deployment shows in its log before a dealer
    reports it (rule 10). The original exception stays on the traceback.
    """

    from infisical_sdk.infisical_requests import InfisicalError

    try:
        return fn()
    except (InfisicalError, OSError) as exc:
        status = _status_of(exc)
        logger.warning(
            "secrets manager refused %s (status=%s, %s)", operation, status, type(exc).__name__
        )
        raise SecretsBackendError(operation=operation, status=status) from exc


def _configured() -> bool:
    return bool(
        os.environ.get(_ENV_PROJECT_ID) and os.environ.get(_ENV_CLIENT_ID) and os.environ.get(_ENV_CLIENT_SECRET)
    )


@lru_cache
def _login():
    from infisical_sdk import InfisicalSDKClient

    client = InfisicalSDKClient(host=os.environ.get(_ENV_HOST, "https://app.infisical.com"))
    client.auth.universal_auth.login(
        client_id=os.environ[_ENV_CLIENT_ID], client_secret=os.environ[_ENV_CLIENT_SECRET]
    )
    return client


def _client():
    """The process's one logged-in client. A refused login is a
    `SecretsBackendError` and is not cached: the next call logs in again."""

    return _call("login", _login)


def _secret_path(connection_id: uuid.UUID) -> str:
    return f"{_SECRET_PATH_PREFIX}/{connection_id}/"


def _environment_slug() -> str:
    return os.environ.get(_ENV_ENVIRONMENT, "prod")


def secret_ref_for(*, connection_id: uuid.UUID, slot: str) -> str:
    """The pointer this module's callers store in `integration_secret_ref.
    secret_ref` — a path string, computable without any Infisical call.
    """

    return f"{_secret_path(connection_id)}{slot}"


def _folder_exists(client, *, name: str, parent_path: str) -> bool:
    listing = _call(
        "list_folders",
        lambda: client.folders.list_folders(
            project_id=os.environ[_ENV_PROJECT_ID], environment_slug=_environment_slug(), path=parent_path
        ),
    )
    return any(folder.name == name for folder in listing.folders)


def _ensure_folder(client, *, name: str, parent_path: str) -> None:
    """Create `parent_path/name` unless it exists — idempotently.

    Infisical creates no missing folder on a secret write (KAN-604: the first
    secret of every connection answered 404 «Folder … not found»). A create
    that fails because a concurrent request created the same folder first
    (two connections set up at once both make `/integrations`) is no error:
    the folder is listed again, and only a folder still missing re-raises.
    """

    if _folder_exists(client, name=name, parent_path=parent_path):
        return
    try:
        _call(
            "create_folder",
            lambda: client.folders.create_folder(
                name=name,
                environment_slug=_environment_slug(),
                project_id=os.environ[_ENV_PROJECT_ID],
                path=parent_path,
            ),
        )
    except SecretsBackendError:
        if _folder_exists(client, name=name, parent_path=parent_path):
            return
        raise


def create_secret(*, connection_id: uuid.UUID, slot: str, value: str) -> str:
    """A connection's first write of a slot. Makes `/integrations` and
    `/integrations/<connection id>` first, where they are missing (KAN-604)."""

    if not _configured():
        raise SecretsBackendNotConfigured()
    client = _client()
    _ensure_folder(client, name=_ROOT_FOLDER, parent_path="/")
    _ensure_folder(client, name=str(connection_id), parent_path=_SECRET_PATH_PREFIX)
    _call(
        "create_secret",
        lambda: client.secrets.create_secret_by_name(
            secret_name=slot,
            secret_path=_secret_path(connection_id),
            environment_slug=_environment_slug(),
            project_id=os.environ[_ENV_PROJECT_ID],
            secret_value=value,
        ),
    )
    return secret_ref_for(connection_id=connection_id, slot=slot)


def update_secret(*, connection_id: uuid.UUID, slot: str, value: str) -> str:
    """"Rotate" — replaces, never reads (Integrations & API Credentials
    v0.1, rule 2)."""

    if not _configured():
        raise SecretsBackendNotConfigured()
    client = _client()
    _call(
        "update_secret",
        lambda: client.secrets.update_secret_by_name(
            current_secret_name=slot,
            secret_path=_secret_path(connection_id),
            environment_slug=_environment_slug(),
            project_id=os.environ[_ENV_PROJECT_ID],
            secret_value=value,
        ),
    )
    return secret_ref_for(connection_id=connection_id, slot=slot)


def delete_secret(*, connection_id: uuid.UUID, slot: str) -> None:
    """Idempotent: a secret the secrets manager no longer holds (404) is
    already deleted, so a delete retried after a partial failure goes
    through instead of refusing for ever."""

    if not _configured():
        raise SecretsBackendNotConfigured()
    client = _client()
    try:
        _call(
            "delete_secret",
            lambda: client.secrets.delete_secret_by_name(
                secret_name=slot,
                secret_path=_secret_path(connection_id),
                environment_slug=_environment_slug(),
                project_id=os.environ[_ENV_PROJECT_ID],
            ),
        )
    except SecretsBackendError as exc:
        if exc.status != 404:
            raise


def delete_connection_folder(*, connection_id: uuid.UUID) -> None:
    """Remove `/integrations/<connection id>` once the connection is deleted
    (KAN-604: no empty folder left behind). A folder that never existed —
    the connection never got a secret — is no error. `/integrations` itself
    stays: every other connection's folder lives under it.

    `force_delete`: Infisical refuses to delete a folder that still holds
    anything. The folder is wholly this connection's, and a secret the
    database holds no reference to (a write whose commit then failed, one
    added by hand) would otherwise block the connection's deletion for
    good."""

    if not _configured():
        raise SecretsBackendNotConfigured()
    client = _client()
    try:
        _call(
            "delete_folder",
            lambda: client.folders.delete_folder(
                folder_id_or_name=str(connection_id),
                environment_slug=_environment_slug(),
                project_id=os.environ[_ENV_PROJECT_ID],
                path=_SECRET_PATH_PREFIX,
                force_delete=True,
            ),
        )
    except SecretsBackendError as exc:
        if exc.status != 404:
            raise


def resolve_secret(*, connection_id: uuid.UUID, slot: str) -> str:
    """In-memory only, per call, by the owning gateway only (rule 3). Never
    persisted anywhere but the process's own call stack. Callers MUST be
    inside app.integration.adapters — see this module's own docstring.
    """

    if not _configured():
        raise SecretsBackendNotConfigured()
    client = _client()
    secret = _call(
        "get_secret",
        lambda: client.secrets.get_secret_by_name(
            secret_name=slot,
            secret_path=_secret_path(connection_id),
            environment_slug=_environment_slug(),
            project_id=os.environ[_ENV_PROJECT_ID],
        ),
    )
    return secret.secretValue

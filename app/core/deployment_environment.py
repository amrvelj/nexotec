"""KAN-499 — one vocabulary for `DMS_ENVIRONMENT`, validated at startup.

Before, three readers each interpreted `settings.environment` their own way:
the provider gateway counted anything but a known non-production name as
production (KAN-489), the object store only exactly ``"production"``
(KAN-402), the mailer anything but exactly ``"development"`` (KAN-397). A
deployment set to ``prod`` was production for one and not for another, and an
unset variable read as ``development`` everywhere, so a production server
that forgot it used the provider's test access, the local object store and
the logging mailer.

Ruling (architect's call, ADR-126; board clean-up 2026-11-11): the value is
one of four names, and

- an unknown value refuses to start, whatever the process;
- an **unset** value refuses to start too, unless the process runs under
  pytest or in local development, where it reads as ``development``. Local
  development is recognised by a ``.env`` file in the working directory: the
  dev-only secrets file `make env` and `scripts/dev/bootstrap` write, which
  `.dockerignore` keeps out of every image. Docker Compose and CI's jobs that
  start the app name their environment explicitly.

The check runs where a process starts serving: `app/main.py` before its
mailer and object store are resolved, and the worker's ``__main__``.
`app/core/config.py` is preserved verbatim, so the setting itself stays a
plain string there and is parsed here.
"""

import enum
import os
import sys
from pathlib import Path

from app.core.config import Settings, get_settings


class DeploymentEnvironment(enum.StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class InvalidDeploymentEnvironmentError(RuntimeError):
    pass


_NAMES = ", ".join(member.value for member in DeploymentEnvironment)


def parse_deployment_environment(value: str) -> DeploymentEnvironment:
    """Exactly one of the four names, compared trimmed and lower-cased."""

    try:
        return DeploymentEnvironment(value.strip().lower())
    except ValueError:
        raise InvalidDeploymentEnvironmentError(
            f"DMS_ENVIRONMENT={value!r} is not one of {_NAMES} (KAN-499)."
        ) from None


def current_deployment_environment(settings: Settings | None = None) -> DeploymentEnvironment:
    """The parsed value the readers act on. A process that passed
    `require_valid_deployment_environment` always has a valid one; anything
    else that does not parse **fails closed**: it reads as production, so
    every production-only refusal (the provider's test access, the local
    object store, the logging mailer) still holds."""

    try:
        return parse_deployment_environment((settings or get_settings()).environment)
    except InvalidDeploymentEnvironmentError:
        return DeploymentEnvironment.PRODUCTION


def _runs_under_pytest() -> bool:
    return "pytest" in sys.modules


def _runs_in_local_development(cwd: Path) -> bool:
    return (cwd / ".env").is_file()


def require_valid_deployment_environment(settings: Settings | None = None, *, cwd: Path | None = None) -> None:
    """Refuses to start (raises) on an unknown value, and on an unset one
    outside pytest and local development."""

    settings = settings or get_settings()
    parse_deployment_environment(settings.environment)
    if "environment" in settings.model_fields_set:
        return
    if _runs_under_pytest() or _runs_in_local_development(cwd or Path(os.getcwd())):
        return
    raise InvalidDeploymentEnvironmentError(
        f"DMS_ENVIRONMENT is not set. A deployed process names its environment ({_NAMES}); only pytest "
        "and local development (a .env file in the working directory) may leave it unset (KAN-499)."
    )

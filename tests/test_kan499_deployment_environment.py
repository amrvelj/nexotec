"""KAN-499: `DMS_ENVIRONMENT` is one of four names, validated at startup, and
read the same way by every reader.

Before, the gateway counted anything but a known non-production name as
production, the object store only exactly "production", the mailer anything
but exactly "development": `prod` was production for the gateway and not for
the object store, and an unset value read as "development" everywhere, so a
server that forgot it ran on the provider's test access, the local object
store and the logging mailer.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from app.core import deployment_environment as env_module
from app.core import object_store
from app.core.config import Settings, get_settings
from app.core.deployment_environment import (
    DeploymentEnvironment,
    InvalidDeploymentEnvironmentError,
    current_deployment_environment,
    parse_deployment_environment,
    require_valid_deployment_environment,
)

_REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["development", "test", "staging", "production"])
def test_each_of_the_four_names_parses(name):
    assert parse_deployment_environment(name) is DeploymentEnvironment(name)


@pytest.mark.parametrize("name", ["prod", "local", "preview", "", "dev", "Production", " staging"])
def test_any_other_name_is_refused_at_startup(name):
    settings = Settings(environment=name)
    with pytest.raises(InvalidDeploymentEnvironmentError, match="KAN-499"):
        require_valid_deployment_environment(settings)


@pytest.mark.parametrize("name", ["prod", "local", "Prodution", "Development"])
def test_a_name_that_does_not_parse_reads_as_production(name):
    assert current_deployment_environment(Settings(environment=name)) is DeploymentEnvironment.PRODUCTION


def _unset_settings(monkeypatch) -> Settings:
    monkeypatch.delenv("DMS_ENVIRONMENT", raising=False)
    settings = Settings(_env_file=None)
    assert "environment" not in settings.model_fields_set
    return settings


def test_an_unset_value_refuses_to_start_outside_pytest_and_local_development(monkeypatch, tmp_path):
    settings = _unset_settings(monkeypatch)
    monkeypatch.setattr(env_module, "_runs_under_pytest", lambda: False)
    with pytest.raises(InvalidDeploymentEnvironmentError, match="not set"):
        require_valid_deployment_environment(settings, cwd=tmp_path)


def test_an_unset_value_is_development_in_a_checkout_with_a_dot_env(monkeypatch, tmp_path):
    settings = _unset_settings(monkeypatch)
    monkeypatch.setattr(env_module, "_runs_under_pytest", lambda: False)
    (tmp_path / ".env").write_text("DMS_X=1\n")
    require_valid_deployment_environment(settings, cwd=tmp_path)
    assert current_deployment_environment(settings) is DeploymentEnvironment.DEVELOPMENT


def test_an_unset_value_is_development_under_pytest(monkeypatch, tmp_path):
    require_valid_deployment_environment(_unset_settings(monkeypatch), cwd=tmp_path)


def test_a_set_value_starts_without_a_dot_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DMS_ENVIRONMENT", "staging")
    monkeypatch.setattr(env_module, "_runs_under_pytest", lambda: False)
    require_valid_deployment_environment(Settings(_env_file=None), cwd=tmp_path)


@pytest.mark.parametrize("name", ["production", "prod", "Production"])
def test_the_object_store_counts_every_production_spelling_as_production(monkeypatch, name):
    """The object store used to compare with exactly "production", so `prod`
    let the local directory store run on a production server (KAN-402)."""

    monkeypatch.setattr(get_settings(), "environment", name)
    monkeypatch.setattr(object_store, "_store", None)
    with pytest.raises(RuntimeError, match="KAN-402"):
        object_store.get_object_store()


@pytest.mark.parametrize("name", ["development", "test", "staging"])
def test_the_object_store_runs_outside_production(monkeypatch, tmp_path, name):
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", name)
    monkeypatch.setattr(settings, "object_store_local_dir", str(tmp_path))
    monkeypatch.setattr(object_store, "_store", None)
    assert object_store.get_object_store() is not None


def test_the_logging_mailer_runs_in_development(monkeypatch):
    from app.platform.services import mailer

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "platform_mail_sender", "logging")
    monkeypatch.setattr(mailer, "_mailer", None)
    assert isinstance(mailer.get_mailer(), mailer.LoggingMailer)


@pytest.mark.parametrize("name", ["test", "staging", "production", "prod", "Development"])
def test_the_logging_mailer_is_refused_on_every_other_value(monkeypatch, name):
    """`test` too, as before KAN-499 (KAN-397: only development keeps the
    logging sender); a value that does not parse is refused like production."""

    from app.platform.services import mailer

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", name)
    monkeypatch.setattr(settings, "platform_mail_sender", "logging")
    monkeypatch.setattr(mailer, "_mailer", None)
    with pytest.raises(RuntimeError, match="must be 'smtp'"):
        mailer.get_mailer()


@pytest.mark.parametrize("name", ["test", "staging", "development"])
def test_the_gateway_uses_a_sandbox_connection_off_production(db_session, monkeypatch, name):
    from app.integration.models.call_log import IntegrationCallLog
    from app.integration.services import gateway
    from tests.test_integration_gateway import _make_connection, _make_provider

    monkeypatch.setattr(get_settings(), "environment", name)
    connection = _make_connection(db_session, _make_provider(db_session))  # a sandbox connection
    with gateway.call_capability(db_session, connection=connection, capability="vehicle_data") as adapter:
        adapter.fetch_variant("FZ100001")
    assert db_session.query(IntegrationCallLog).count() == 1


@pytest.mark.parametrize("name", ["production", "prod", "local", "Staging"])
def test_the_gateway_refuses_a_sandbox_connection_on_production_or_an_unparsed_name(db_session, monkeypatch, name):
    from app.integration.services import gateway
    from tests.test_integration_gateway import _make_connection, _make_provider

    monkeypatch.setattr(get_settings(), "environment", name)
    connection = _make_connection(db_session, _make_provider(db_session))
    with pytest.raises(gateway.SandboxConnectionOnProductionError), gateway.call_capability(
        db_session, connection=connection, capability="vehicle_data"
    ) as adapter:
        adapter.fetch_variant("FZ100001")


def test_every_deployed_service_in_render_yaml_names_a_valid_environment():
    blueprint = yaml.safe_load((_REPO / "render.yaml").read_text())
    named = [
        var["value"]
        for service in blueprint.get("services", [])
        for var in service.get("envVars", [])
        if var.get("key") == "DMS_ENVIRONMENT"
    ]
    assert named, "render.yaml's services name no DMS_ENVIRONMENT"
    for value in named:
        parse_deployment_environment(value)


def _startup_env(**overrides: str) -> dict[str, str]:
    settings = get_settings()
    env = {k: v for k, v in os.environ.items() if not k.startswith("DMS_")}
    env.update(
        DMS_TAX_ID_ENCRYPTION_KEY=settings.tax_id_encryption_key,
        DMS_JWT_PRIVATE_KEY=settings.jwt_private_key,
        DMS_DATABASE_URL=settings.database_url,
        PYTHONPATH=str(_REPO),
    )
    env.update(overrides)
    return env


def test_the_web_app_refuses_to_start_with_an_unset_environment_and_no_dot_env(tmp_path):
    started = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=tmp_path, env=_startup_env(),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert started.returncode != 0
    assert "DMS_ENVIRONMENT is not set" in started.stderr


def test_the_web_app_refuses_to_start_with_an_unknown_environment(tmp_path):
    started = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=tmp_path, env=_startup_env(DMS_ENVIRONMENT="prod"),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert started.returncode != 0
    assert "DMS_ENVIRONMENT='prod' is not one of" in started.stderr


def test_the_web_app_starts_with_a_named_environment(tmp_path):
    started = subprocess.run(
        [sys.executable, "-c", "import app.main"], cwd=tmp_path, env=_startup_env(DMS_ENVIRONMENT="development"),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert started.returncode == 0, started.stderr


def test_the_worker_refuses_to_start_with_an_unset_environment_and_no_dot_env(tmp_path):
    started = subprocess.run(
        [sys.executable, "-m", "app.worker"], cwd=tmp_path, env=_startup_env(DMS_OUTBOX_WORKER_MAX_ITERATIONS="1"),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert started.returncode != 0
    assert "DMS_ENVIRONMENT is not set" in started.stderr

"""KAN-485 — no provider is ever asked for its whole catalogue.

Anto's ruling, 2026-10-29: the auto-i-dat agreement permits single lookups
only (identAPI test agreement §2, §9: no retrieval of all vehicles, no storing
or passing them on), and the demo provider follows the same rule. ADR-023 is
amended accordingly. The full seed and the daily `FzKeyChanged` delta were
removed; this guard keeps them out:

  1. No adapter offers the changed-keys list a mirror is built on
     (`list_changed_keys`, the `FzKeyChanged` Datenname).
  2. Nothing under `app/` or `scripts/` calls or names it, and the old bulk
     entry points (`seed_tenant_catalogue`, `run_daily_delta_for_tenant`) do
     not come back under those names.
  3. No daily job reaches a provider at all.

Self-tests prove the scanner catches each shape.
"""

import ast
import uuid
from pathlib import Path

import pytest

from app import worker
from app.core import daily_scheduler
from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter
from app.integration.adapters.base import ProviderAdapter
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import gateway

_REPO = Path(__file__).resolve().parent.parent.parent
_SCANNED_ROOTS = (_REPO / "app", _REPO / "scripts")

_FORBIDDEN_DATENNAMEN = {"FzKeyChanged"}
_FORBIDDEN_NAMES = {"list_changed_keys", "seed_tenant_catalogue", "run_daily_delta_for_tenant"}


def _violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _FORBIDDEN_DATENNAMEN:
            found.append(f"string {node.value!r} (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            found.append(f"attribute .{node.attr} (line {node.lineno})")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            found.append(f"name {node.id} (line {node.lineno})")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in _FORBIDDEN_NAMES:
            found.append(f"def {node.name} (line {node.lineno})")
        elif isinstance(node, ast.alias) and node.name.split(".")[-1] in _FORBIDDEN_NAMES:
            found.append(f"import {node.name} (line {getattr(node, 'lineno', '?')})")
    return found


def _python_files() -> list[Path]:
    return sorted(path for root in _SCANNED_ROOTS for path in root.rglob("*.py"))


def test_the_scan_covers_the_adapters_and_the_sync():
    files = {path.relative_to(_REPO).as_posix() for path in _python_files()}
    for expected in (
        "app/integration/adapters/auto_i_dat_soap.py",
        "app/integration/adapters/auto_i_dat_mock.py",
        "app/vehicle/services/catalogue_sync.py",
        "app/integration/daily_jobs.py",
        "scripts/verify_auto_i_dat.py",
    ):
        assert expected in files, expected


@pytest.mark.parametrize("adapter", [ProviderAdapter, MockAutoIDatAdapter, AutoIDatSoapAdapter], ids=lambda a: a.__name__)
def test_no_adapter_offers_the_changed_keys_list(adapter):
    assert not hasattr(adapter, "list_changed_keys"), (
        f"{adapter.__name__} offers list_changed_keys again — FzKeyChanged is what a full catalogue mirror is "
        "built on, which the provider contract forbids (KAN-485, ADR-023 as amended 2026-10-29)."
    )


def test_nothing_in_app_or_scripts_retrieves_the_whole_catalogue():
    offending = {
        path.relative_to(_REPO).as_posix(): hits
        for path in _python_files()
        if (hits := _violations(path.read_text()))
    }
    assert not offending, (
        "Bulk catalogue retrieval is back — single lookups only (KAN-485, ADR-023 as amended 2026-10-29): "
        f"{offending}"
    )


def test_no_daily_job_reaches_a_provider(db_session, monkeypatch):
    """Every registered daily job, run against a tenant with an enabled demo
    connection, never resolves a provider adapter — the step every provider
    call goes through (`gateway._adapter_factory`). The daily catalogue delta
    used to be the first thing `integration.daily_jobs` did.
    """

    provider = IntegrationProvider(
        provider_code="auto_i_dat_mock", category="vehicle_data", display_name="auto-i-dat (mock)",
        auth_type="none", required_secret_slots=[], capability_codes=["vehicle_data"],
    )
    db_session.add(provider)
    db_session.commit()
    connection_service.create_connection(
        db_session, tenant_id=uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )

    resolved: list[uuid.UUID] = []

    def _record(db, connection):
        resolved.append(connection.id)
        raise AssertionError("a daily job reached a provider")

    monkeypatch.setattr(gateway, "_adapter_factory", _record)
    monkeypatch.setattr(daily_scheduler, "_REGISTRY", {})
    worker.register_daily_jobs()
    jobs = dict(daily_scheduler._REGISTRY)
    assert "integration.daily_jobs" in jobs, "the integration daily job is not registered — the guard would pass vacuously"
    for name, job in jobs.items():
        try:
            job(db_session)
        except AssertionError:
            raise
        except Exception:  # noqa: BLE001 - a job failing for another reason is not this guard's question
            db_session.rollback()
        assert not resolved, f"daily job {name!r} reached a provider: single lookups only (KAN-485)"


# -- self-tests: the scanner catches each shape ------------------------------


@pytest.mark.parametrize(
    "source",
    [
        'adapter._suchen("FzKeyChanged", {})',
        "keys = adapter.list_changed_keys(since=today)",
        "def list_changed_keys(self, *, since): ...",
        "def seed_tenant_catalogue(db, *, tenant_id): ...",
        "from app.vehicle.public import run_daily_delta_for_tenant",
        "run_daily_delta_for_tenant(db, tenant_id=tenant_id)",
    ],
)
def test_the_scanner_catches(source):
    assert _violations(source)


def test_the_scanner_leaves_single_lookups_alone():
    assert not _violations(
        'adapter._suchen("Fahrzeuge", {"FzKey": fz_key})\n'
        "sync_catalogue_variant(db, tenant_id=tenant_id, fz_key=fz_key)\n"
        '"""A docstring may mention FzKeyChanged in prose."""\n'
    )

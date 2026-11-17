"""KAN-752: every config key a provider requires has a label in all four UI bundles.

The connect dialog labels each of a provider's `required_config_keys` with
`integrationEnums.configKey.<key>` (`IntegrationDealerView.tsx`), and the
`missing_config_fields` refusal names them the same way (`utils/apiRefusal.ts`). The
provider rows are seeded by integration migrations with literal values (ADR-133), so
nothing tied them to the bundles: AutoScout24's `kundennummer`, `ftpHost` and
`ftpUsername` rendered «⚠ MISSING I18N KEY», and DAT's `accountNumber` too.

The truth is the migrated registry, not a list copied into this test: it runs
`alembic upgrade heads` on a throwaway database next to the test database (the
pattern of `tests/test_migrated_notification_catalogue.py`) and reads every provider
row, so the next provider migration that names a new key fails here until the key is
in all four bundles.
"""

import json
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LOCALES = _REPO_ROOT / "frontend" / "apps" / "dms" / "src" / "i18n" / "locales"
_LANGUAGES = ("de", "fr", "it", "en")

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


@pytest.fixture(scope="module")
def migrated_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan752_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = sqlalchemy_url_for(base, name)
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "heads"],
            cwd=_REPO_ROOT,
            env=dict(os.environ, DMS_DATABASE_URL=url),
            capture_output=True,
            text=True,
            timeout=UPGRADE_TIMEOUT_S,
            check=False,
        )
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _migrated_config_keys(url: str) -> dict[str, list[str]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT provider_code, required_config_keys FROM integration_provider")).all()
    finally:
        engine.dispose()
    return {code: list(keys or []) for code, keys in rows}


def _bundle_config_keys(language: str) -> set[str]:
    bundle = json.loads((_LOCALES / language / "integrationEnums.json").read_text(encoding="utf-8"))
    return {key for key, value in bundle.get("configKey", {}).items() if isinstance(value, str) and value.strip()}


def test_every_required_config_key_of_every_migrated_provider_has_a_label_in_all_four_bundles(migrated_url):
    providers = _migrated_config_keys(migrated_url)
    # The registry is seeded: an empty read would make this test pass vacuously.
    assert providers.get("autoscout24"), providers
    missing = sorted(
        (language, code, key)
        for language in _LANGUAGES
        for code, keys in providers.items()
        for key in keys
        if key not in _bundle_config_keys(language)
    )
    assert missing == [], (
        "integrationEnums.configKey.<key> is missing for (language, provider, key): "
        f"{missing} — the connect dialog would render the MISSING I18N KEY marker"
    )


def test_the_autoscout24_labels_are_the_ones_the_ticket_names():
    de = json.loads((_LOCALES / "de" / "integrationEnums.json").read_text(encoding="utf-8"))["configKey"]
    assert (de["kundennummer"], de["ftpHost"], de["ftpUsername"]) == ("Kundennummer", "FTP-Server", "FTP-Benutzername")

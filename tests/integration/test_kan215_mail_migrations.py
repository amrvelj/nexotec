"""KAN-215, F5 of the 2026-11-15 review: the two migrations go down with data
in their tables, and refuse — naming what to do — only where a row would
orphan secret material (the KAN-550 rule). Runs `alembic` on a throwaway
database built from empty, like CI's migration job."""

import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ, DMS_DATABASE_URL=url),
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )


@pytest.fixture(scope="module")
def migrated_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan215_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        url = sqlalchemy_url_for(base, name)
        upgrade = _alembic(url, "upgrade", "heads")
        assert upgrade.returncode == 0, upgrade.stderr[-3000:]
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _execute(url: str, *statements: str):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = None
            for statement in statements:
                result = conn.execute(text(statement))
            return result.all() if result is not None and result.returns_rows else None
    finally:
        engine.dispose()


def test_the_integration_migration_goes_down_with_mailboxes_and_deliveries_and_up_again(migrated_url):
    identity = uuid.uuid4()
    _execute(
        migrated_url,
        "INSERT INTO integration_sending_identity (id, version, created_at, updated_at, tenant_id, kind, provider_code, "
        "mailbox_address, external_account_id, provider_tenant, status, connected_at) VALUES "
        f"('{identity}', 1, now(), now(), '{uuid.uuid4()}', 'business_mailbox', 'microsoft_365_mail', "
        "'info@garage-muster.ch', 'obj', 'tid', 'active', now())",
        "INSERT INTO integration_message_delivery (id, created_at, updated_at, tenant_id, sending_identity_id, "
        "identity_kind, sender_address, recipients, subject, document_type, author_user_id, status, sent_at) VALUES "
        f"('{uuid.uuid4()}', now(), now(), '{uuid.uuid4()}', '{identity}', 'business_mailbox', 'info@garage-muster.ch', "
        f"'[\"kunde@example.test\"]', 'Offerte', 'offer', '{uuid.uuid4()}', 'sent', now())",
        "INSERT INTO integration_mail_link_state (id, created_at, updated_at, state_hash, tenant_id, user_id, kind, "
        f"provider_code, leg, expires_at) VALUES ('{uuid.uuid4()}', now(), now(), '{'a' * 64}', '{uuid.uuid4()}', "
        f"'{uuid.uuid4()}', 'user_mailbox', 'google_workspace_mail', 'user_signin', now())",
    )

    down = _alembic(migrated_url, "downgrade", "integration@e7a2c91d4b60")
    assert down.returncode == 0, down.stderr[-3000:]
    assert _execute(migrated_url, "SELECT to_regclass('integration_sending_identity') IS NULL") == [(True,)]
    assert _execute(
        migrated_url, "SELECT count(*) FROM integration_provider WHERE category = 'calendar_mail'"
    ) == [(0,)]

    up = _alembic(migrated_url, "upgrade", "heads")
    assert up.returncode == 0, up.stderr[-3000:]
    assert _execute(
        migrated_url, "SELECT provider_code FROM integration_provider WHERE category = 'calendar_mail' ORDER BY 1"
    ) == [("google_workspace_mail",), ("microsoft_365_mail",)]


def test_the_downgrades_refuse_where_a_row_would_orphan_a_secret(migrated_url):
    connection = uuid.uuid4()
    _execute(
        migrated_url,
        "INSERT INTO integration_connection (id, version, created_at, updated_at, provider_id, scope, display_name, "
        f"environment, config, enabled, status) SELECT '{connection}', 1, now(), now(), id, 'platform', 'Nexotec', "
        "'production', '{}', true, 'connected' FROM integration_provider WHERE provider_code = 'microsoft_365_mail'",
    )
    refused = _alembic(migrated_url, "downgrade", "integration@e7a2c91d4b60")
    assert refused.returncode != 0 and "connection(s) to a mailbox provider exist" in refused.stderr
    _execute(migrated_url, f"DELETE FROM integration_connection WHERE id = '{connection}'")

    # platform: a Google Workspace grant may reference a token in the secrets manager.
    grant = uuid.uuid4()
    _execute(
        migrated_url,
        "SET session_replication_role = replica",  # no user row needed for the refusal (the test role is a superuser)
        "INSERT INTO user_connected_account (id, version, created_at, updated_at, user_id, provider, "
        "external_account_id, scopes, secret_ref, status, linked_at) VALUES "
        f"('{grant}', 1, now(), now(), '{uuid.uuid4()}', 'google_workspace', 'g-sub', '[]', "
        f"'/integrations/{grant}/refresh_token', 'active', now())",
    )
    refused = _alembic(migrated_url, "downgrade", "platform@b8d2f4a61c39")
    assert refused.returncode != 0 and "Google Workspace connected account(s) exist" in refused.stderr
    _execute(migrated_url, f"DELETE FROM user_connected_account WHERE id = '{grant}'")
    down = _alembic(migrated_url, "downgrade", "platform@b8d2f4a61c39")
    assert down.returncode == 0, down.stderr[-3000:]
    up = _alembic(migrated_url, "upgrade", "heads")
    assert up.returncode == 0, up.stderr[-3000:]

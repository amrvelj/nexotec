"""KAN-217 PR-5 (integration 5e1b7c3a9d20): every existing delivery becomes an
e-mail; the downgrade refuses while an SMS or WhatsApp record exists (it is
kept as long as its document, Q3 A) and goes down cleanly once none does.
Runs `alembic` on a throwaway database built from empty, like CI's migration
job."""

import uuid

import pytest

from tests.integration.test_kan215_mail_migrations import _alembic, _execute, migrated_url

__all__ = ["migrated_url"]  # the module-scoped fixture, used by name

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)"
)

_BEFORE = "integration@8c4f19b2d471"


def _identity(url) -> uuid.UUID:
    identity = uuid.uuid4()
    _execute(
        url,
        "INSERT INTO integration_sending_identity (id, version, created_at, updated_at, tenant_id, kind, provider_code, "
        "mailbox_address, external_account_id, provider_tenant, status, connected_at) VALUES "
        f"('{identity}', 1, now(), now(), '{uuid.uuid4()}', 'business_mailbox', 'microsoft_365_mail', "
        f"'info-{identity.hex[:6]}@garage-muster.ch', 'obj', 'tid-{identity.hex[:6]}', 'active', now())",
    )
    return identity


def test_existing_deliveries_become_emails_and_an_sms_holds_the_downgrade(migrated_url):
    down = _alembic(migrated_url, "downgrade", _BEFORE)
    assert down.returncode == 0, down.stderr[-3000:]
    identity = _identity(migrated_url)
    old = uuid.uuid4()
    _execute(
        migrated_url,
        "INSERT INTO integration_message_delivery (id, created_at, updated_at, tenant_id, sending_identity_id, "
        "identity_kind, sender_address, recipients, subject, document_type, author_user_id, status, sent_at) VALUES "
        f"('{old}', now(), now(), '{uuid.uuid4()}', '{identity}', 'business_mailbox', 'info@garage-muster.ch', "
        f"'[\"kunde@example.ch\"]', 'Offerte', 'offer', '{uuid.uuid4()}', 'sent', now())",
    )
    up = _alembic(migrated_url, "upgrade", "heads")
    assert up.returncode == 0, up.stderr[-3000:]
    assert _execute(migrated_url, f"SELECT channel FROM integration_message_delivery WHERE id = '{old}'") == [("email",)]

    connection = uuid.uuid4()
    _execute(
        migrated_url,
        "INSERT INTO integration_connection (id, version, created_at, updated_at, provider_id, scope, tenant_id, "
        f"display_name, environment, config, enabled, status) SELECT '{connection}', 1, now(), now(), id, 'tenant', "
        f"'{uuid.uuid4()}', 'Twilio', 'production', '{{}}', true, 'connected' FROM integration_provider "
        "WHERE provider_code = 'twilio'",
    )
    sms = uuid.uuid4()
    _execute(
        migrated_url,
        "INSERT INTO integration_message_delivery (id, created_at, updated_at, tenant_id, channel, connection_id, "
        "recipients, body_text, document_type, author_user_id, status, sent_at) VALUES "
        f"('{sms}', now(), now(), '{uuid.uuid4()}', 'sms', '{connection}', '[\"+41790000217\"]', 'Ihre Offerte', "
        f"'offer', '{uuid.uuid4()}', 'sent', now())",
    )
    refused = _alembic(migrated_url, "downgrade", _BEFORE)
    assert refused.returncode != 0 and "SMS or WhatsApp delivery record(s) exist" in refused.stderr

    _execute(
        migrated_url,
        f"DELETE FROM integration_message_delivery WHERE id = '{sms}'",
        f"DELETE FROM integration_connection WHERE id = '{connection}'",
    )
    assert _alembic(migrated_url, "downgrade", _BEFORE).returncode == 0
    assert _alembic(migrated_url, "upgrade", "heads").returncode == 0


def test_an_sms_row_without_a_connection_is_refused_by_the_check(migrated_url):
    with pytest.raises(Exception, match="ck_integration_message_delivery_channel_source"):
        _execute(
            migrated_url,
            "INSERT INTO integration_message_delivery (id, created_at, updated_at, tenant_id, channel, recipients, "
            f"document_type, author_user_id, status, sent_at) VALUES ('{uuid.uuid4()}', now(), now(), '{uuid.uuid4()}', "
            f"'sms', '[]', 'offer', '{uuid.uuid4()}', 'sent', now())",
        )

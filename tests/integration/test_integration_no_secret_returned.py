"""KAN-222 (FR-A-19, «no endpoint returns a secret»): the integration
registry is write-only at the secret boundary.

Two guards: no response schema of any `/v1/integrations/*` route has a field
that could carry a secret (checked over the OpenAPI document, so a new route
is covered the day it ships), and a connection whose secret was set answers
every read without the value or its reference anywhere in the body.
"""

import uuid

from app.core.auth import create_access_token
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.main import app
from tests.token_support import dealer_group_of

# Field names that would carry a secret or the pointer to one.
_SECRET_FIELD_NAMES = {
    "secret",
    "secretValue",
    "secretRef",
    "value",
    "password",
    "clientSecret",
    "accessToken",
    "refreshToken",
    "token",
    "apiKey",
}


def _schema_fields(schema: dict, components: dict, seen: set[str]) -> set[str]:
    names: set[str] = set()
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return names
        seen.add(name)
        return _schema_fields(components[name], components, seen)
    for key in ("allOf", "anyOf", "oneOf"):
        for part in schema.get(key, []):
            names |= _schema_fields(part, components, seen)
    if "items" in schema:
        names |= _schema_fields(schema["items"], components, seen)
    for prop, sub in schema.get("properties", {}).items():
        names.add(prop)
        names |= _schema_fields(sub, components, seen)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        names |= _schema_fields(additional, components, seen)
    return names


def test_no_integration_response_schema_has_a_secret_field():
    document = app.openapi()
    components = document["components"]["schemas"]
    offending: list[str] = []
    checked = 0
    for path, operations in document["paths"].items():
        if not path.startswith("/v1/integrations"):
            continue
        for method, operation in operations.items():
            for status, response in operation.get("responses", {}).items():
                if not status.startswith("2"):
                    continue
                for media in response.get("content", {}).values():
                    checked += 1
                    fields = _schema_fields(media.get("schema", {}), components, set())
                    offending += [f"{method.upper()} {path} {status}: {f}" for f in fields & _SECRET_FIELD_NAMES]
    assert checked > 5, "no integration response schema found — did the routes move?"
    assert offending == []


def test_no_read_returns_the_secret_or_its_reference(client, db_session, monkeypatch):
    from app.integration.services import secrets_backend

    monkeypatch.setattr(secrets_backend, "create_secret", lambda *, connection_id, slot, value: f"ref://{slot}/s3cr3t-ref")
    provider = IntegrationProvider(
        provider_code="autoscout24", category="marketplace", display_name="AutoScout24", auth_type="password",
        required_secret_slots=["password"], required_config_keys=["kundennummer"], capability_codes=[],
    )
    db_session.add(provider)
    db_session.commit()
    tenant_id = uuid.uuid4()
    connection = connection_service.create_connection(
        db_session, tenant_id=tenant_id,
        data=ConnectionCreate(
            provider_id=provider.id, display_name="AS24", environment=ConnectionEnvironment.PRODUCTION,
            config={"kundennummer": "K-1"},
        ),
        actor_id=None,
    )
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    headers = {"Authorization": f"Bearer {token}"}

    written = client.put(
        f"/v1/integrations/connections/{connection.id}/secrets/password",
        json={"secretValue": "hunter2-plain"},
        headers=headers,
    )
    assert written.status_code == 200, written.text

    bodies = [
        written.text,
        client.get(f"/v1/integrations/connections/{connection.id}", headers=headers).text,
        client.get("/v1/integrations/connections", headers=headers).text,
        client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=headers).text,
    ]
    for body in bodies:
        assert "hunter2-plain" not in body
        assert "s3cr3t-ref" not in body

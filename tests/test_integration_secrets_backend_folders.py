"""KAN-604: the Infisical secrets backend creates the per-connection folder.

Infisical does not create a missing folder on a secret write. Before this
ticket `secrets_backend.create_secret` wrote `/integrations/<connection id>/`
straight away, so the first secret of every new connection answered
`APIError: Folder with path '/integrations/<id>' … not found (Status: 404)`
and `PUT /v1/integrations/connections/{id}/secrets/{slot}` a 500 — on
staging on 2026-11-09, and on every deployment with an Infisical project.
Nothing caught it: every other test runs with the backend unconfigured or
replaces the whole module.

These tests run the real `secrets_backend` module against `FakeInfisical`,
an in-memory client with the SDK's own surface (`secrets`, `folders`) that
behaves as Infisical does where it matters: a secret write into a folder
that does not exist is a 404 `APIError`, creating a folder that exists is a
400 `APIError`, and deleting something that is not there is a 404.
"""

import logging
import uuid
from types import SimpleNamespace

import pytest
import requests
from infisical_sdk.infisical_requests import APIError

from app.core.auth import create_access_token
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.models.secret_ref import SecretSlot
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import secrets_backend
from tests.token_support import dealer_group_of

PROJECT = "test-project-0001"
ENVIRONMENT = "staging"


def _api_error(message: str, status: int) -> APIError:
    return APIError(message=message, status_code=status, response={"message": message})


def _join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


def _norm(path: str) -> str:
    return "/" + path.strip("/") if path.strip("/") else "/"


class _Folders:
    def __init__(self, fake: "FakeInfisical") -> None:
        self._fake = fake

    def list_folders(self, *, project_id, environment_slug, path, **_):
        self._fake.check(project_id, environment_slug)
        parent = _norm(path)
        if parent not in self._fake.folders:
            raise _api_error(f"Folder with path '{parent}' not found", 404)
        children = [p for p in self._fake.folders if p != "/" and _norm(p.rsplit("/", 1)[0]) == parent]
        return SimpleNamespace(folders=[SimpleNamespace(name=p.rsplit("/", 1)[1]) for p in children])

    def create_folder(self, *, name, environment_slug, project_id, path="/", **_):
        self._fake.check(project_id, environment_slug)
        self._fake.create_folder_calls.append(_join(_norm(path), name))
        if self._fake.create_folder_race is not None:
            # Another request created this very folder between our list and our create.
            self._fake.folders.add(self._fake.create_folder_race)
            self._fake.create_folder_race = None
        parent = _norm(path)
        target = _join(parent, name)
        if parent not in self._fake.folders:
            raise _api_error(f"Folder with path '{parent}' not found", 404)
        if target in self._fake.folders:
            raise _api_error(f"Folder with name '{name}' already exists", 400)
        self._fake.folders.add(target)
        return SimpleNamespace(name=name, path=target)

    def delete_folder(self, *, folder_id_or_name, environment_slug, project_id, path="/", force_delete=False, **_):
        self._fake.check(project_id, environment_slug)
        target = _join(_norm(path), folder_id_or_name)
        if target not in self._fake.folders:
            raise _api_error(f"Folder with path '{target}' not found", 404)
        held = [k for k in self._fake.secrets if k[0] == target]
        if held and not force_delete:
            # Infisical refuses to delete a folder that still holds anything.
            raise _api_error(f"Cannot delete folder {folder_id_or_name} because it contains resources", 400)
        self._fake.folders.discard(target)
        for key in held:
            del self._fake.secrets[key]


class _Secrets:
    def __init__(self, fake: "FakeInfisical") -> None:
        self._fake = fake

    def _folder(self, secret_path: str) -> str:
        folder = _norm(secret_path)
        if folder not in self._fake.folders:
            raise _api_error(f"Folder with path '{folder}' in environment with slug '{ENVIRONMENT}' not found", 404)
        return folder

    def create_secret_by_name(self, *, secret_name, secret_path, environment_slug, project_id, secret_value, **_):
        self._fake.check(project_id, environment_slug)
        if self._fake.unreachable:
            # What the SDK lets through when no answer came: `requests`' own error.
            raise requests.exceptions.ConnectionError(f"cannot reach host for {secret_path}{secret_name}")
        if self._fake.fail_secret_writes is not None:
            raise _api_error(f"refused {secret_path}{secret_name}", self._fake.fail_secret_writes)
        folder = self._folder(secret_path)
        if (folder, secret_name) in self._fake.secrets:
            raise _api_error("Secret already exists", 400)
        self._fake.secrets[(folder, secret_name)] = secret_value

    def update_secret_by_name(self, *, current_secret_name, secret_path, environment_slug, project_id, secret_value, **_):
        self._fake.check(project_id, environment_slug)
        folder = self._folder(secret_path)
        if (folder, current_secret_name) not in self._fake.secrets:
            raise _api_error("Secret not found", 404)
        self._fake.secrets[(folder, current_secret_name)] = secret_value

    def delete_secret_by_name(self, *, secret_name, secret_path, environment_slug, project_id, **_):
        self._fake.check(project_id, environment_slug)
        folder = self._folder(secret_path)
        if (folder, secret_name) not in self._fake.secrets:
            raise _api_error("Secret not found", 404)
        del self._fake.secrets[(folder, secret_name)]

    def get_secret_by_name(self, *, secret_name, secret_path, environment_slug, project_id, **_):
        self._fake.check(project_id, environment_slug)
        folder = self._folder(secret_path)
        if (folder, secret_name) not in self._fake.secrets:
            raise _api_error("Secret not found", 404)
        return SimpleNamespace(secretValue=self._fake.secrets[(folder, secret_name)])


class FakeInfisical:
    def __init__(self) -> None:
        self.folders: set[str] = {"/"}
        self.secrets: dict[tuple[str, str], str] = {}
        self.create_folder_calls: list[str] = []
        self.create_folder_race: str | None = None
        self.fail_secret_writes: int | None = None
        self.unreachable = False
        self.folders_api = _Folders(self)
        self.secrets_api = _Secrets(self)

    def check(self, project_id: str, environment_slug: str) -> None:
        assert project_id == PROJECT
        assert environment_slug == ENVIRONMENT


@pytest.fixture
def infisical(monkeypatch) -> FakeInfisical:
    fake = FakeInfisical()
    client = SimpleNamespace(folders=fake.folders_api, secrets=fake.secrets_api)
    monkeypatch.setenv("DMS_INFISICAL_PROJECT_ID", PROJECT)
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_ID", "test-client-0001")
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_SECRET", "test-client-secret-0001")
    monkeypatch.setenv("DMS_INFISICAL_ENVIRONMENT", ENVIRONMENT)
    monkeypatch.setattr(secrets_backend, "_client", lambda: client)
    return fake


def _provider(db_session) -> IntegrationProvider:
    provider = IntegrationProvider(
        provider_code="auto_i_dat",
        category="vehicle_data",
        display_name="auto-i-dat",
        auth_type="oauth2_user_enrollment",
        required_secret_slots=["client_secret"],
        capability_codes=["ident_basis"],
    )
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _connection(db_session, provider: IntegrationProvider, tenant_id: uuid.UUID | None = None):
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(provider_id=provider.id, display_name="identAPI", environment=ConnectionEnvironment.SANDBOX),
        actor_id=uuid.uuid4(),
    )


def _set(db_session, connection, value: str = "s3cr3t-value", slot: SecretSlot = SecretSlot.CLIENT_SECRET):
    return connection_service.set_secret(db_session, connection=connection, slot=slot, value=value, actor_id=uuid.uuid4())


def test_the_first_secret_of_a_new_connection_creates_both_folders_and_is_stored(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))

    ref = _set(db_session, connection)

    assert "/integrations" in infisical.folders
    assert f"/integrations/{connection.id}" in infisical.folders
    assert infisical.secrets[(f"/integrations/{connection.id}", "client_secret")] == "s3cr3t-value"
    assert ref.secret_ref == f"/integrations/{connection.id}/client_secret"
    # The value goes to the secrets manager and nowhere else.
    assert "s3cr3t-value" not in ref.secret_ref
    assert secrets_backend.resolve_secret(connection_id=connection.id, slot="client_secret") == "s3cr3t-value"


def test_a_second_connection_reuses_the_integrations_folder(db_session, infisical):
    provider = _provider(db_session)
    first = _connection(db_session, provider)
    second = _connection(db_session, provider)

    _set(db_session, first)
    _set(db_session, second)

    assert infisical.create_folder_calls == ["/integrations", f"/integrations/{first.id}", f"/integrations/{second.id}"]
    assert {f"/integrations/{first.id}", f"/integrations/{second.id}"} <= infisical.folders


def test_a_second_slot_of_the_same_connection_creates_no_folder(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))
    _set(db_session, connection, slot=SecretSlot.CLIENT_SECRET)
    calls_after_first = list(infisical.create_folder_calls)

    _set(db_session, connection, slot=SecretSlot.PASSWORD, value="pw")

    assert infisical.create_folder_calls == calls_after_first
    assert infisical.secrets[(f"/integrations/{connection.id}", "password")] == "pw"


def test_an_existing_folder_is_not_an_error(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))
    # Made by hand, as on staging on 2026-11-09.
    infisical.folders |= {"/integrations", f"/integrations/{connection.id}"}

    _set(db_session, connection)

    assert infisical.create_folder_calls == []
    assert infisical.secrets[(f"/integrations/{connection.id}", "client_secret")] == "s3cr3t-value"


def test_a_folder_a_concurrent_request_created_first_is_not_an_error(db_session, infisical):
    """Two connections set up at once both find `/integrations` missing; the
    loser's create answers «already exists» — that request goes through."""

    connection = _connection(db_session, _provider(db_session))
    infisical.create_folder_race = "/integrations"

    _set(db_session, connection)

    assert infisical.secrets[(f"/integrations/{connection.id}", "client_secret")] == "s3cr3t-value"


def test_rotation_replaces_the_value_in_the_same_folder(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))
    _set(db_session, connection, value="first")

    _set(db_session, connection, value="second")

    assert infisical.secrets[(f"/integrations/{connection.id}", "client_secret")] == "second"


def test_deleting_the_connection_removes_its_folder_and_keeps_the_others(db_session, infisical):
    provider = _provider(db_session)
    kept = _connection(db_session, provider)
    deleted = _connection(db_session, provider)
    _set(db_session, kept)
    _set(db_session, deleted)
    _set(db_session, deleted, slot=SecretSlot.PASSWORD, value="pw")

    connection_service.delete_connection(db_session, connection=deleted, confirm=True, actor_id=uuid.uuid4())

    assert f"/integrations/{deleted.id}" not in infisical.folders
    assert not [key for key in infisical.secrets if key[0] == f"/integrations/{deleted.id}"]
    assert "/integrations" in infisical.folders
    assert infisical.secrets[(f"/integrations/{kept.id}", "client_secret")] == "s3cr3t-value"


def test_a_secret_the_database_does_not_know_does_not_block_deleting_the_connection(db_session, infisical):
    """A secret in the connection's folder with no reference row — a write
    whose commit then failed, one added by hand — is deleted with the folder
    instead of leaving the connection undeletable."""

    connection = _connection(db_session, _provider(db_session))
    _set(db_session, connection)
    infisical.secrets[(f"/integrations/{connection.id}", "certificate")] = "stray"

    connection_service.delete_connection(db_session, connection=connection, confirm=True, actor_id=uuid.uuid4())

    assert f"/integrations/{connection.id}" not in infisical.folders
    assert not [key for key in infisical.secrets if key[0] == f"/integrations/{connection.id}"]


def test_deleting_a_connection_that_never_had_a_secret_is_not_an_error(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))

    connection_service.delete_connection(db_session, connection=connection, confirm=True, actor_id=uuid.uuid4())

    assert infisical.folders == {"/"}


def test_a_delete_retried_after_the_secret_is_already_gone_goes_through(db_session, infisical):
    """A secret the store no longer holds is already deleted (404): a retried
    delete — after a failure half-way through — must not refuse for ever."""

    connection = _connection(db_session, _provider(db_session))
    _set(db_session, connection)
    del infisical.secrets[(f"/integrations/{connection.id}", "client_secret")]

    connection_service.delete_connection(db_session, connection=connection, confirm=True, actor_id=uuid.uuid4())

    assert f"/integrations/{connection.id}" not in infisical.folders


def test_an_infisical_refusal_on_write_is_a_typed_refusal_not_a_500(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))
    infisical.fail_secret_writes = 403

    with pytest.raises(secrets_backend.SecretsBackendError) as raised:
        _set(db_session, connection)

    assert raised.value.status_code == 409
    assert raised.value.details == {"reason": "secrets_backend_failed", "operation": "create_secret", "status": 403}
    # Neither the value nor the secret's path reaches the message.
    assert "s3cr3t-value" not in raised.value.message
    assert str(connection.id) not in raised.value.message


def test_an_unreachable_secrets_manager_is_a_typed_refusal_not_a_500(db_session, infisical):
    connection = _connection(db_session, _provider(db_session))
    infisical.unreachable = True

    with pytest.raises(secrets_backend.SecretsBackendError) as raised:
        _set(db_session, connection)

    assert raised.value.details == {"reason": "secrets_backend_failed", "operation": "create_secret", "status": None}
    assert str(connection.id) not in raised.value.message


def test_a_refusal_is_logged_for_the_operator_without_the_value_or_the_path(db_session, infisical, caplog):
    connection = _connection(db_session, _provider(db_session))
    infisical.fail_secret_writes = 403

    with caplog.at_level(logging.WARNING, logger=secrets_backend.__name__), pytest.raises(secrets_backend.SecretsBackendError):
        _set(db_session, connection)

    lines = [r.getMessage() for r in caplog.records if r.name == secrets_backend.__name__]
    assert lines == ["secrets manager refused create_secret (status=403, APIError)"]
    assert "s3cr3t-value" not in caplog.text
    assert str(connection.id) not in caplog.text


def test_an_answer_the_backend_absorbs_logs_no_refusal(db_session, infisical, caplog):
    """A delete of what is already gone and a folder a concurrent request
    created first are successes: the operator's log says nothing."""

    provider = _provider(db_session)
    raced = _connection(db_session, provider)
    never_had_one = _connection(db_session, provider)
    infisical.create_folder_race = "/integrations"

    with caplog.at_level(logging.WARNING, logger=secrets_backend.__name__):
        _set(db_session, raced)
        del infisical.secrets[(f"/integrations/{raced.id}", "client_secret")]
        connection_service.delete_connection(db_session, connection=raced, confirm=True, actor_id=uuid.uuid4())
        connection_service.delete_connection(db_session, connection=never_had_one, confirm=True, actor_id=uuid.uuid4())

    assert [r for r in caplog.records if r.name == secrets_backend.__name__] == []


def test_an_infisical_refusal_reaches_the_dealer_as_409_with_the_standard_body(client, db_session, infisical):
    provider = _provider(db_session)
    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "identAPI", "environment": "sandbox"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]
    infisical.fail_secret_writes = 500

    response = client.put(
        f"/v1/integrations/connections/{connection_id}/secrets/client_secret",
        json={"secretValue": "s3cr3t-value"},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["details"] == {"reason": "secrets_backend_failed", "operation": "create_secret", "status": 500}
    assert "s3cr3t-value" not in response.text
    assert f"/integrations/{connection_id}" not in response.text


def test_the_first_secret_through_the_api_answers_200_without_a_hand_made_folder(client, db_session, infisical):
    """Exit criterion 4's code path, on the fake: the staging 500 of 2026-11-09."""

    provider = _provider(db_session)
    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=dealer_group_of(str(tenant_id)),
        roles=frozenset(), is_dealer_manager=True,
    )
    headers = {"Authorization": f"Bearer {token}"}
    connection_id = client.post(
        "/v1/integrations/connections",
        json={"providerId": str(provider.id), "displayName": "identAPI", "environment": "sandbox"},
        headers=headers,
    ).json()["id"]

    response = client.put(
        f"/v1/integrations/connections/{connection_id}/secrets/client_secret",
        json={"secretValue": "s3cr3t-value"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert "s3cr3t-value" not in response.text
    assert f"/integrations/{connection_id}" in infisical.folders


def test_a_refused_login_is_a_typed_refusal_and_is_not_cached(monkeypatch):
    """The real `_login`, its SDK client swapped for one whose login is
    refused: each call is a typed refusal, and the next call logs in again
    rather than reusing a cached failure."""

    import infisical_sdk

    monkeypatch.setenv("DMS_INFISICAL_PROJECT_ID", PROJECT)
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_ID", "test-client-0001")
    monkeypatch.setenv("DMS_INFISICAL_CLIENT_SECRET", "test-client-secret-0001")
    attempts: list[str] = []

    class RefusingClient:
        def __init__(self, host: str) -> None:
            def login(*, client_id: str, client_secret: str) -> None:
                attempts.append(client_id)
                raise _api_error("Invalid credentials", 401)

            self.auth = SimpleNamespace(universal_auth=SimpleNamespace(login=login))

    monkeypatch.setattr(infisical_sdk, "InfisicalSDKClient", RefusingClient)
    secrets_backend._login.cache_clear()
    try:
        for _ in range(2):
            with pytest.raises(secrets_backend.SecretsBackendError) as raised:
                secrets_backend.create_secret(connection_id=uuid.uuid4(), slot="client_secret", value="v")
            assert raised.value.details == {"reason": "secrets_backend_failed", "operation": "login", "status": 401}
    finally:
        secrets_backend._login.cache_clear()
    assert attempts == ["test-client-0001", "test-client-0001"]

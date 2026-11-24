"""KAN-227 (D-A-33, FR-A-38): the AI prompt library — the standard texts'
seed-and-override pattern on a closed set of codes — and `generate`, the one
door to the Betrieb's own AI provider, with its smallest consumer, Stock's
«Text vorschlagen».

The provider is a fake transport behind the real adapters and the real
gateway, so the call log, the tokens on it and the usage endpoint are the
real ones."""

import importlib.util
import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole
from app.core.errors import UnprocessableEntityError
from app.core.i18n import SwissLanguage
from app.integration.adapters import ai_providers
from app.integration.models.ai_prompt import AiPromptSource, IntegrationAiPrompt
from app.integration.models.call_log import IntegrationCallLog
from app.integration.models.connection import (
    ConnectionEnvironment,
    ConnectionScope,
    ConnectionStatus,
    IntegrationConnection,
)
from app.integration.models.provider import IntegrationProvider
from app.integration.services import ai_prompts as service
from app.integration.services import resilience, secrets_backend
from app.integration.services.ai_prompt_registry import NOT_STATED, VEHICLE_LISTING, render, require_code
from app.integration.services.ai_prompt_seed import SEED_PROMPTS
from app.inventory.models.stock_item_publishing import StockItemPublishing
from tests.inventory.test_inventory_invoicing_gate import _make_dealership
from tests.inventory.test_inventory_marketplace_transmission import _make_ready_item
from tests.membership_support import bearer, token
from tests.vin_support import unique_vin

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic/versions/integration/e7a2c91d4b60_kan_227_ai_prompts_provider_rows_token_.py"


@pytest.fixture
def seeds(db_session) -> int:
    added = service.install_seeds(db_session)
    db_session.commit()
    return added


@pytest.fixture
def dealership(db_session):
    return _make_dealership(db_session)


def _manager(dealership_id) -> dict[str, str]:
    return bearer(token(tenant_id=dealership_id, is_dealer_manager=True))


def _role(dealership_id, role: AccessRole) -> dict[str, str]:
    return bearer(token(role, tenant_id=dealership_id))


# --- the seeds ---------------------------------------------------------------------


def _migration_module():
    spec = importlib.util.spec_from_file_location("kan227_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_migrations_seed_rows_are_the_shipped_prompts():
    """ADR-133: literals in the migration, equal to what the application reads
    as the shipped wording (the Translations panel diffs and reverts to it)."""

    migrated = set(_migration_module()._SEED_PROMPTS)
    shipped = {
        (seed.code, language.value, seed.titles[language], seed.system_prompts[language], seed.user_prompts[language])
        for seed in SEED_PROMPTS
        for language in SwissLanguage
    }
    assert migrated == shipped


def test_the_migration_seeds_the_four_ai_providers_byo_key_without_sandbox():
    providers = {code: keys for code, _name, keys, _docs in _migration_module()._PROVIDERS}
    assert providers == {
        "openai": ["model"],
        "azure_openai": ["endpoint", "deployment", "apiVersion"],
        "mistral": ["model"],
        "anthropic": ["model"],
    }
    assert set(providers) == set(ai_providers.AI_PROVIDER_CODES)


def test_vehicle_listing_is_seeded_in_four_languages_with_only_registered_placeholders(seeds):
    entry = require_code(VEHICLE_LISTING)
    assert seeds == 4
    for seed in SEED_PROMPTS:
        for language in SwissLanguage:
            # Nothing outside the registry, every fact the caller sends is used.
            text = seed.user_prompts[language]
            render(entry, text, values={}, language=language)
            assert all(f"{{{{ {name} }}}}" in text for name in entry.placeholders)
    # Swiss spelling — the one «ß» is the instruction never to write one.
    german = SEED_PROMPTS[0].system_prompts[SwissLanguage.DE] + SEED_PROMPTS[0].user_prompts[SwissLanguage.DE]
    assert "ß" not in german.replace("«ß»", "")


def test_a_missing_fact_is_said_never_guessed_and_an_unknown_token_never_reaches_a_provider():
    entry = require_code(VEHICLE_LISTING)
    assert render(entry, "Farbe: {{ vehicle.exterior_colour }}", values={}, language=SwissLanguage.FR) == (
        f"Farbe: {NOT_STATED[SwissLanguage.FR]}"
    )
    with pytest.raises(UnprocessableEntityError) as caught:
        render(entry, "{{ contact.name }}", values={"contact.name": "x"}, language=SwissLanguage.DE)
    assert caught.value.details["reason"] == "ai_prompt_placeholder_unresolved"


# --- the dealer's library -------------------------------------------------------------


def test_the_library_shows_seeds_codes_and_that_no_provider_is_connected(client, seeds, dealership):
    response = client.get(f"/v1/dealerships/{dealership.id}/ai-prompts", headers=_role(dealership.id, AccessRole.INVENTORY))
    assert response.status_code == 200, response.text
    body = response.json()
    assert {(row["code"], row["language"], row["source"]) for row in body["items"]} == {
        (VEHICLE_LISTING, language.value, "platform_seed") for language in SwissLanguage
    }
    assert body["codes"] == [{"code": VEHICLE_LISTING, "placeholders": list(require_code(VEHICLE_LISTING).placeholders)}]
    assert body["connection"] is None


def test_another_betriebs_library_is_404(client, seeds, dealership):
    other = uuid.uuid4()
    response = client.get(f"/v1/dealerships/{dealership.id}/ai-prompts", headers=_manager(other))
    assert response.status_code == 404


def test_only_the_manager_adapts_a_prompt(client, seeds, dealership):
    body = {"code": VEHICLE_LISTING, "language": "de", "systemPrompt": "Kurz.", "userPrompt": "{{ vehicle.label }}"}
    for role in (AccessRole.INVENTORY, AccessRole.SALES):
        refused = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_role(dealership.id, role))
        assert refused.status_code == 403, role
    created = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id))
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["source"] == "dealer" and row["seedChanged"] is False and row["overriddenFromId"] is not None


def test_an_adaptation_refuses_a_placeholder_its_code_does_not_list(client, seeds, dealership):
    body = {"code": VEHICLE_LISTING, "language": "de", "systemPrompt": "S", "userPrompt": "{{ contact.email }}"}
    response = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id))
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "ai_prompt_placeholder_invalid"


def test_a_second_adaptation_of_the_same_language_is_409(client, seeds, dealership):
    body = {"code": VEHICLE_LISTING, "language": "it", "systemPrompt": "S", "userPrompt": "U"}
    assert client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id)).status_code == 201
    again = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "ai_prompt_exists"


def test_a_seed_edit_flags_the_adaptation_until_reviewed_and_never_overwrites_it(client, db_session, seeds, dealership):
    body = {"code": VEHICLE_LISTING, "language": "de", "systemPrompt": "Unsere Fassung.", "userPrompt": "{{ vehicle.label }}"}
    own = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id)).json()
    seed = db_session.get(IntegrationAiPrompt, uuid.UUID(own["overriddenFromId"]))
    staff = bearer(token(AccessRole.PLATFORM_ADMIN))

    edited = client.put(
        f"/v1/platform/ai-prompt-seeds/{seed.id}",
        json={"systemPrompt": "Neue Vorgabe."},
        headers={**staff, "If-Match": str(seed.version)},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["edited"] is True

    listed = client.get(f"/v1/dealerships/{dealership.id}/ai-prompts", headers=_manager(dealership.id)).json()
    mine = next(row for row in listed["items"] if row["id"] == own["id"])
    assert mine["seedChanged"] is True and mine["systemPrompt"] == "Unsere Fassung."

    reviewed = client.patch(
        f"/v1/dealerships/{dealership.id}/ai-prompts/{own['id']}",
        json={"seedReviewed": True},
        headers={**_manager(dealership.id), "If-Match": str(mine["version"])},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["seedChanged"] is False and reviewed.json()["systemPrompt"] == "Unsere Fassung."

    reverted = client.post(
        f"/v1/platform/ai-prompt-seeds/{seed.id}/revert", headers={**staff, "If-Match": str(edited.json()["version"])}
    )
    assert reverted.status_code == 200 and reverted.json()["edited"] is False


def test_a_dealer_cannot_write_a_seed(client, db_session, seeds, dealership):
    seed = db_session.scalar(select(IntegrationAiPrompt).where(IntegrationAiPrompt.tenant_id.is_(None)))
    response = client.put(
        f"/v1/platform/ai-prompt-seeds/{seed.id}",
        json={"systemPrompt": "x"},
        headers={**_manager(dealership.id), "If-Match": str(seed.version)},
    )
    assert response.status_code == 403
    # Nor through the dealer routes: a seed is not the Betrieb's row.
    patched = client.patch(
        f"/v1/dealerships/{dealership.id}/ai-prompts/{seed.id}",
        json={"systemPrompt": "x"},
        headers={**_manager(dealership.id), "If-Match": str(seed.version)},
    )
    assert patched.status_code == 404


def test_back_to_the_default_removes_the_adaptation(client, seeds, dealership):
    body = {"code": VEHICLE_LISTING, "language": "en", "systemPrompt": "S", "userPrompt": "U"}
    own = client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=body, headers=_manager(dealership.id)).json()
    deleted = client.delete(
        f"/v1/dealerships/{dealership.id}/ai-prompts/{own['id']}",
        headers={**_manager(dealership.id), "If-Match": str(own["version"])},
    )
    assert deleted.status_code == 204
    listed = client.get(f"/v1/dealerships/{dealership.id}/ai-prompts", headers=_manager(dealership.id)).json()
    assert all(row["source"] == "platform_seed" for row in listed["items"])


# --- generate and «Text vorschlagen» -------------------------------------------------------


class FakeProvider:
    def __init__(self, text: str = "Gepflegter Škoda Octavia Kombi in Blau metallic.", status: int = 200):
        self.text = text
        self.status = status
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "provider detail"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": self.text}}], "usage": {"prompt_tokens": 310, "completion_tokens": 96}},
        )


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(ai_providers, "http_transport", lambda: httpx.MockTransport(fake))
    monkeypatch.setattr(secrets_backend, "resolve_secret", lambda *, connection_id, slot: "sk-test")
    return fake


def _ai_connection(db_session, tenant_id, *, provider_code: str = "openai", status=ConnectionStatus.CONNECTED):
    row = db_session.scalar(select(IntegrationProvider).where(IntegrationProvider.provider_code == provider_code))
    if row is None:
        row = IntegrationProvider(
            provider_code=provider_code, category="ai", display_name=provider_code.capitalize(), auth_type="api_key",
            required_secret_slots=["api_key"], required_config_keys=["model"], capability_codes=["ai_text_generation"],
            supports_sandbox=False,
        )
        db_session.add(row)
        db_session.flush()
    connection = IntegrationConnection(
        provider_id=row.id, scope=ConnectionScope.TENANT, tenant_id=tenant_id, display_name=f"{provider_code} account",
        environment=ConnectionEnvironment.PRODUCTION, config={"model": "dealer-model"}, enabled=True, status=status,
    )
    db_session.add(connection)
    db_session.commit()
    resilience.record_success(connection.id)
    return connection


def _suggest(client, item, headers, **body):
    return client.post(f"/v1/inventory/stock-items/{item.id}/listing-text-suggestions", json=body, headers=headers)


def test_text_vorschlagen_sends_the_cars_facts_and_stores_nothing(client, db_session, seeds, dealership, provider):
    connection = _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    publishing_rows = db_session.scalar(select(func.count()).select_from(StockItemPublishing))

    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "text": "Gepflegter Škoda Octavia Kombi in Blau metallic.",
        "language": "de",
        "promptAdapted": False,
        "providerName": "Openai",
    }
    sent = provider.requests[0]["messages"]
    assert sent[0]["role"] == "system" and "Schweizer Rechtschreibung" in sent[0]["content"]
    facts = sent[1]["content"]
    for fact in ("Škoda Octavia", "Occasion", "Kombi", "Blau metallic", "42'000 km", "01.03.2021", "CHF 19'900.00"):
        assert fact in facts, fact
    assert "{{" not in facts
    # A suggestion only: no listing text written, no publishing row created.
    assert db_session.scalar(select(func.count()).select_from(StockItemPublishing)) == publishing_rows
    logged = db_session.scalars(select(IntegrationCallLog).where(IntegrationCallLog.connection_id == connection.id)).all()
    assert [(r.capability, r.tokens_in, r.tokens_out) for r in logged] == [("chat.completions", 310, 96)]


def _equip(db_session, item, *, extras, eigenschaften, provider_ausstattung):
    from app.vehicle.models.vehicle_mdm import VehicleMdm

    vehicle = db_session.get(VehicleMdm, item.vehicle_id)
    vehicle.extras = extras
    vehicle.eigenschaften = eigenschaften
    vehicle.provider_ausstattung = provider_ausstattung
    db_session.commit()


def test_the_provider_equipment_text_in_the_prompts_language_is_sent_never_the_condition_flags(
    client, db_session, seeds, dealership, provider
):
    _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    _equip(
        db_session, item,
        extras=["Anhängerkupplung"],
        eigenschaften=["Unfallwagen", "Nichtraucher"],
        provider_ausstattung={"de": "Navigationssystem, Sitzheizung", "fr": "Système de navigation, sièges chauffants"},
    )

    assert _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="fr").status_code == 200

    facts = provider.requests[0]["messages"][1]["content"]
    assert "Système de navigation, sièges chauffants" in facts
    # The language's provider text wins over the extras, and never another language's.
    assert "Navigationssystem" not in facts and "Anhängerkupplung" not in facts
    # Condition flags are not equipment: an accident history is never sent.
    assert "Unfallwagen" not in facts and "Nichtraucher" not in facts


def test_without_a_provider_text_in_the_language_the_extras_are_sent(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    _equip(
        db_session, item,
        extras=["Anhängerkupplung", "Panoramadach"],
        eigenschaften=["Unfallwagen"],
        provider_ausstattung={"de": "Navigationssystem"},
    )

    assert _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="it").status_code == 200

    facts = provider.requests[0]["messages"][1]["content"]
    assert "Anhängerkupplung, Panoramadach" in facts
    assert "Navigationssystem" not in facts and "Unfallwagen" not in facts


def test_without_any_equipment_the_prompt_says_not_stated(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    _equip(db_session, item, extras=[], eigenschaften=["Unfallwagen"], provider_ausstattung={})

    assert _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de").status_code == 200

    facts = provider.requests[0]["messages"][1]["content"]
    assert f"Ausstattung: {NOT_STATED[SwissLanguage.DE]}" in facts and "Unfallwagen" not in facts


def test_the_betriebs_adaptation_and_the_language_asked_for_are_used(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, dealership.id)
    adapted = {
        "code": VEHICLE_LISTING,
        "language": "fr",
        "systemPrompt": "Style maison.",
        "userPrompt": "Voiture : {{ vehicle.label }} — {{ vehicle.condition }}",
    }
    assert client.post(f"/v1/dealerships/{dealership.id}/ai-prompts", json=adapted, headers=_manager(dealership.id)).status_code == 201
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())

    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="fr")

    assert response.status_code == 200, response.text
    assert response.json()["language"] == "fr" and response.json()["promptAdapted"] is True
    assert provider.requests[0]["messages"] == [
        {"role": "system", "content": "Style maison."},
        {"role": "user", "content": "Voiture : Škoda Octavia — Occasion"},
    ]


def test_without_a_language_the_betriebs_correspondence_language_speaks(client, db_session, seeds, dealership, provider):
    dealership.default_correspondence_language = SwissLanguage.IT
    db_session.commit()
    _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())

    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY))

    assert response.status_code == 200, response.text
    assert response.json()["language"] == "it"
    assert "Occasione" in provider.requests[0]["messages"][1]["content"]


def test_a_betrieb_without_an_ai_connection_gets_ai_not_connected(client, db_session, seeds, dealership, provider):
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "ai_not_connected"
    assert provider.requests == []


def test_another_betriebs_ai_connection_is_never_used(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, uuid.uuid4())
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de")
    assert response.status_code == 409 and provider.requests == []


def test_a_failing_provider_is_a_422_in_our_words(client, db_session, seeds, dealership, provider):
    provider.status = 503
    connection = _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())

    response = _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de")

    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "ai_provider_unavailable"
    assert "provider detail" not in response.text
    # `failure` is our adapter's wording; no `providerMessage` key claims otherwise.
    details = response.json()["error"]["details"]
    assert details["failure"] == "OpenAI failed to answer (HTTP 503)." and "providerMessage" not in details
    resilience.record_success(connection.id)


def test_sales_may_read_the_prompts_but_not_ask_for_a_listing_text(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    assert _suggest(client, item, _role(dealership.id, AccessRole.SALES), language="de").status_code == 403
    assert provider.requests == []


def test_the_connection_in_use_is_the_most_recently_tested_enabled_one(client, db_session, seeds, dealership, provider):
    _ai_connection(db_session, dealership.id, provider_code="mistral", status=ConnectionStatus.ERROR)
    preferred = _ai_connection(db_session, dealership.id, provider_code="openai")
    listed = client.get(f"/v1/dealerships/{dealership.id}/ai-prompts", headers=_manager(dealership.id)).json()
    assert listed["connection"]["connectionId"] == str(preferred.id)
    assert listed["connection"]["providerCode"] == "openai" and listed["connection"]["status"] == "connected"


def test_the_usage_view_counts_the_tokens(client, db_session, seeds, dealership, provider):
    connection = _ai_connection(db_session, dealership.id)
    item = _make_ready_item(db_session, dealership.id, vin=unique_vin())
    for _ in range(2):
        assert _suggest(client, item, _role(dealership.id, AccessRole.INVENTORY), language="de").status_code == 200

    usage = client.get(f"/v1/integrations/connections/{connection.id}/usage", headers=_manager(dealership.id)).json()

    assert usage["totalCalls"] == 2
    assert usage["counters"] == [{"counter": "chat.completions", "count": 2}]
    assert (usage["tokensIn"], usage["tokensOut"]) == (620, 192)


def test_an_override_row_is_always_the_betriebs_and_a_seed_never_is(db_session, seeds, dealership):
    seed = db_session.scalar(select(IntegrationAiPrompt).where(IntegrationAiPrompt.tenant_id.is_(None)))
    assert seed.source == AiPromptSource.PLATFORM_SEED
    db_session.add(
        IntegrationAiPrompt(
            tenant_id=None, code=VEHICLE_LISTING, language=SwissLanguage.DE, title="t", system_prompt="s",
            user_prompt="u", source=AiPromptSource.DEALER, overridden_from_id=seed.id,
        )
    )
    with pytest.raises(Exception, match="ck_integration_ai_prompt_source_owner"):
        db_session.flush()
    db_session.rollback()

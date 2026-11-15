"""KAN-227 (D-A-33): the Betrieb's own AI provider — the four provider rows,
the `api_key` secret slot, token counts on the call log, and the AI prompt
library with Nexotec's `vehicle_listing` seed in DE/FR/IT/EN.

- `integration_provider`: `openai`, `azure_openai`, `mistral`, `anthropic`,
  category `ai`, slot `api_key`, the model (Azure: endpoint, deployment,
  API version) as the dealer's configuration. No sandbox: a provider key has
  no test mode.
- `integration_secret_ref.slot` gains `api_key` (`ck_integration_secret_ref_slot_enum`).
- `integration_call_log.tokens_in` / `tokens_out` (FR-A-36's usage counters).
- `integration_ai_prompt` and its seed rows as literals (ADR-133);
  `tests/test_ai_prompts.py` keeps them equal to
  `app/integration/services/ai_prompt_seed.py`.

The downgrade refuses while a connection to an AI provider exists or an
`api_key` reference is held: it does not drop references to secret material it
cannot reach (the KAN-550 rule).

Revision ID: e7a2c91d4b60
Revises: c4e9a2d7f1b3
Create Date: 2026-11-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

from app.core.types import GUID, UTCDateTime
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "e7a2c91d4b60"
down_revision: str | Sequence[str] | None = "c4e9a2d7f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLOT_CHECK = "ck_integration_secret_ref_slot_enum"
_SLOTS_BEFORE = ("password", "client_secret", "refresh_token", "certificate")
_SLOTS_AFTER = ("password", "client_secret", "refresh_token", "certificate", "api_key")

# (provider_code, display_name, required_config_keys, docs_url)
_PROVIDERS: tuple[tuple[str, str, list[str], str], ...] = (
    ("openai", "OpenAI", ["model"], "https://platform.openai.com/docs/api-reference/chat"),
    (
        "azure_openai",
        "Azure OpenAI",
        ["endpoint", "deployment", "apiVersion"],
        "https://learn.microsoft.com/azure/ai-services/openai/reference",
    ),
    ("mistral", "Mistral AI", ["model"], "https://docs.mistral.ai/api/"),
    ("anthropic", "Anthropic", ["model"], "https://docs.anthropic.com/en/api/messages"),
)
_PROVIDER_CODES = tuple(code for code, *_ in _PROVIDERS)

# (code, language, title, system_prompt, user_prompt)
_SEED_PROMPTS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "vehicle_listing",
        "de",
        "Inseratetext für ein Fahrzeug",
        "Du schreibst Inseratetexte für ein Autohaus in der Schweiz. Schreibe auf Deutsch in Schweizer Rechtschreibung (immer «ss», nie «ß»), sachlich und freundlich, 80 bis 150 Wörter, als Fliesstext ohne Aufzählungszeichen, Überschriften oder Markdown. Verwende ausschliesslich die genannten Angaben. Erfinde nichts: keine Ausstattung, keine Vorgeschichte, keine Unfallfreiheit, keine Garantie, keinen Service und keine Kontaktdaten. Eine Angabe «keine Angabe» lässt du weg.",  # noqa: E501
        "Schreibe den Inseratetext für dieses Fahrzeug.\n\nFahrzeug: {{ vehicle.label }}\nZustand: {{ vehicle.condition }}\nKarosserie: {{ vehicle.body_style }}\nAussenfarbe: {{ vehicle.exterior_colour }}\nKilometerstand: {{ vehicle.odometer_km }}\n1. Inverkehrsetzung: {{ vehicle.first_registration }}\nPreis: {{ vehicle.price }}\nAusstattung: {{ vehicle.equipment }}",  # noqa: E501
    ),
    (
        "vehicle_listing",
        "fr",
        "Texte d'annonce pour un véhicule",
        "Tu rédiges des textes d'annonce pour un garage en Suisse. Écris en français, sur un ton factuel et aimable, entre 80 et 150 mots, en texte continu sans puces, titres ni Markdown. Utilise uniquement les informations fournies. N'invente rien : aucun équipement, aucun historique, aucune absence d'accident, aucune garantie, aucun service et aucune coordonnée. Omets toute information « non indiqué ».",  # noqa: E501
        "Rédige le texte d'annonce de ce véhicule.\n\nVéhicule : {{ vehicle.label }}\nÉtat : {{ vehicle.condition }}\nCarrosserie : {{ vehicle.body_style }}\nCouleur extérieure : {{ vehicle.exterior_colour }}\nKilométrage : {{ vehicle.odometer_km }}\n1re mise en circulation : {{ vehicle.first_registration }}\nPrix : {{ vehicle.price }}\nÉquipement : {{ vehicle.equipment }}",  # noqa: E501
    ),
    (
        "vehicle_listing",
        "it",
        "Testo dell'annuncio per un veicolo",
        "Scrivi testi di annunci per un garage in Svizzera. Scrivi in italiano, con tono oggettivo e cordiale, da 80 a 150 parole, in testo continuo senza elenchi puntati, titoli o Markdown. Usa esclusivamente le informazioni fornite. Non inventare nulla: nessun equipaggiamento, nessuna storia, nessuna assenza di incidenti, nessuna garanzia, nessun servizio e nessun recapito. Ometti ogni informazione «non indicato».",  # noqa: E501
        "Scrivi il testo dell'annuncio per questo veicolo.\n\nVeicolo: {{ vehicle.label }}\nStato: {{ vehicle.condition }}\nCarrozzeria: {{ vehicle.body_style }}\nColore esterno: {{ vehicle.exterior_colour }}\nChilometraggio: {{ vehicle.odometer_km }}\n1a immatricolazione: {{ vehicle.first_registration }}\nPrezzo: {{ vehicle.price }}\nEquipaggiamento: {{ vehicle.equipment }}",  # noqa: E501
    ),
    (
        "vehicle_listing",
        "en",
        "Listing text for a vehicle",
        "You write listing texts for a car dealership in Switzerland. Write in English, factual and friendly, 80 to 150 words, as running text without bullet points, headings or Markdown. Use only the facts given. Invent nothing: no equipment, no history, no accident-free claim, no warranty, no service and no contact details. Leave out any fact marked «not stated».",  # noqa: E501
        "Write the listing text for this vehicle.\n\nVehicle: {{ vehicle.label }}\nCondition: {{ vehicle.condition }}\nBody style: {{ vehicle.body_style }}\nExterior colour: {{ vehicle.exterior_colour }}\nMileage: {{ vehicle.odometer_km }}\nFirst registration: {{ vehicle.first_registration }}\nPrice: {{ vehicle.price }}\nEquipment: {{ vehicle.equipment }}",  # noqa: E501
    ),
)


def _condition(values: Sequence[str]) -> str:
    return '"slot" IN (' + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "integration_ai_prompt",
        sa.Column(
            "tenant_id",
            GUID(),
            nullable=True,
            comment="Owned by the platform context (Dealership.id). No DB-level FK. NULL = a Nexotec seed.",
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("overridden_from_id", GUID(), nullable=True),
        sa.Column("overridden_seed_version", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTime(timezone=True), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("updated_by", GUID(), nullable=True),
        sa.CheckConstraint(
            "(source = 'platform_seed' AND tenant_id IS NULL AND overridden_from_id IS NULL) "
            "OR (source = 'dealer' AND tenant_id IS NOT NULL AND overridden_from_id IS NOT NULL)",
            name="ck_integration_ai_prompt_source_owner",
        ),
        sa.CheckConstraint("language IN ('de', 'fr', 'it', 'en')", name="ck_integration_ai_prompt_language_enum"),
        sa.CheckConstraint("source IN ('platform_seed', 'dealer')", name="ck_integration_ai_prompt_source_enum"),
        sa.ForeignKeyConstraint(["overridden_from_id"], ["integration_ai_prompt.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_integration_ai_prompt_overridden_from_id"), "integration_ai_prompt", ["overridden_from_id"], unique=False
    )
    op.create_index(op.f("ix_integration_ai_prompt_tenant_id"), "integration_ai_prompt", ["tenant_id"], unique=False)
    op.create_index(
        "uq_integration_ai_prompt_seed_code_language",
        "integration_ai_prompt",
        ["code", "language"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
        sqlite_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_integration_ai_prompt_tenant_code_language",
        "integration_ai_prompt",
        ["tenant_id", "code", "language"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
        sqlite_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.add_column("integration_call_log", sa.Column("tokens_in", sa.Integer(), nullable=True))
    op.add_column("integration_call_log", sa.Column("tokens_out", sa.Integer(), nullable=True))
    op.drop_constraint(_SLOT_CHECK, "integration_secret_ref", type_="check")
    op.create_check_constraint(_SLOT_CHECK, "integration_secret_ref", _condition(_SLOTS_AFTER))

    now = sa.func.now()
    provider = sa.table(
        "integration_provider",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("version", sa.Integer()),
        sa.column("provider_code", sa.String()),
        sa.column("category", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("auth_type", sa.String()),
        sa.column("required_secret_slots", sa.JSON()),
        sa.column("required_config_keys", sa.JSON()),
        sa.column("capability_codes", sa.JSON()),
        sa.column("docs_url", sa.String()),
        sa.column("supports_sandbox", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for code, name, config_keys, docs_url in _PROVIDERS:
        op.execute(
            provider.insert().values(
                id=uuid7(),
                version=1,
                provider_code=code,
                category="ai",
                display_name=name,
                auth_type="api_key",
                required_secret_slots=["api_key"],
                required_config_keys=config_keys,
                capability_codes=["ai_text_generation"],
                docs_url=docs_url,
                supports_sandbox=False,
                created_at=now,
                updated_at=now,
            )
        )
    prompt = sa.table(
        "integration_ai_prompt",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("language", sa.String()),
        sa.column("title", sa.String()),
        sa.column("system_prompt", sa.Text()),
        sa.column("user_prompt", sa.Text()),
        sa.column("source", sa.String()),
        sa.column("overridden_from_id", postgresql.UUID(as_uuid=True)),
        sa.column("overridden_seed_version", sa.Integer()),
        sa.column("active", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    for code, language, title, system_prompt, user_prompt in _SEED_PROMPTS:
        op.execute(
            prompt.insert().values(
                id=uuid7(),
                tenant_id=None,
                code=code,
                language=language,
                title=title,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                source="platform_seed",
                overridden_from_id=None,
                overridden_seed_version=None,
                active=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )


def refuse_if_ai_connections_exist(conn: sa.engine.Connection) -> None:
    codes = ", ".join(f"'{code}'" for code in _PROVIDER_CODES)
    held = conn.execute(
        sa.text(
            "SELECT count(*) FROM integration_connection c JOIN integration_provider p ON p.id = c.provider_id "
            f"WHERE p.provider_code IN ({codes})"
        )
    ).scalar_one()
    keys = conn.execute(sa.text("SELECT count(*) FROM integration_secret_ref WHERE slot = 'api_key'")).scalar_one()
    if held or keys:
        raise RuntimeError(
            f"{held} connection(s) to an AI provider and {keys} 'api_key' secret reference(s) exist. Delete the "
            "AI connections on the release still running, then downgrade again: this migration does not drop "
            "references to secret material it cannot reach (KAN-550's rule, KAN-227)."
        )


def downgrade() -> None:
    if context.is_offline_mode():
        op.execute("-- KAN-227 AI connections check skipped: it reads rows and needs a live database")
    else:
        refuse_if_ai_connections_exist(op.get_bind())
    codes = ", ".join(f"'{code}'" for code in _PROVIDER_CODES)
    op.execute(f"DELETE FROM integration_provider WHERE provider_code IN ({codes})")
    op.drop_constraint(_SLOT_CHECK, "integration_secret_ref", type_="check")
    op.create_check_constraint(_SLOT_CHECK, "integration_secret_ref", _condition(_SLOTS_BEFORE))
    op.drop_column("integration_call_log", "tokens_out")
    op.drop_column("integration_call_log", "tokens_in")
    op.drop_index(
        "uq_integration_ai_prompt_tenant_code_language",
        table_name="integration_ai_prompt",
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
        sqlite_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_integration_ai_prompt_seed_code_language",
        table_name="integration_ai_prompt",
        postgresql_where=sa.text("tenant_id IS NULL"),
        sqlite_where=sa.text("tenant_id IS NULL"),
    )
    op.drop_index(op.f("ix_integration_ai_prompt_tenant_id"), table_name="integration_ai_prompt")
    op.drop_index(op.f("ix_integration_ai_prompt_overridden_from_id"), table_name="integration_ai_prompt")
    op.drop_table("integration_ai_prompt")

"""Standard texts: the Textbaustein library (KAN-208)

Dealer Administration PRD "Standard texts" (A-RULE-4, FR-A-07/08/28/29/54,
D-A-23, D-A-26):

- `standard_text`: one row per (scope, code, language) — platform seeds
  (`dealership_id` NULL), a Betrieb's own texts and overrides of seeds
  (`overridden_from_id`, `overridden_seed_version`), and location variants.
  Uniqueness per scope is three partial unique indexes (a NULL never
  collides in a plain unique constraint).
- The seeded starter library (`app.platform.services.standard_text_seed`,
  the same rows the tests install): twelve codes × DE/FR/IT/EN. Inserted
  with `bulk_insert`, so it renders offline too.
- `document_template.footer_text_*` moves into the library: every non-empty
  footer becomes three dealer texts of its Betrieb, one row per language
  each — `invoice.footer.carried_over` (not the default: the seeded payment
  terms stay the invoice footer; the Betrieb picks), and
  `offer.footer.carried_over` / `contract.footer.carried_over` as the
  Betrieb's **default** offer and contract footer, so the offers and
  contracts it prints keep the footer they printed before (the dealer
  default outranks the seed's). A language without a footer had none
  printed; there the seed default now prints — the ticket's stated change,
  like a Betrieb that had no footer at all. Then the four columns are
  dropped.
  Pure SQL (INSERT … SELECT, v4 ids from gen_random_uuid() as KAN-198's and
  KAN-203's backfills), so the data step reads no rows in Python and renders
  offline. Its count is verified by tests/test_kan208_footer_migration.py.
- `standard_text_delegation.category` gains `whatsapp` (FR-A-52/54): the
  CHECK is replaced with the full list (tests/test_migrated_enum_checks.py).

Downgrade copies each carried-over footer back onto its template (creating
the template row where none exists any more), deletes the delegations and
texts of the WhatsApp category the old CHECK refuses, restores the CHECK and
drops the table — the Betrieb's other texts are lost with it (a failed
deploy only).

Revision ID: ec4559d84ad3
Revises: 444ddb84d8c9
Create Date: 2026-10-31 14:24:42.864493

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.base import utcnow
from app.core.uuid7 import uuid7
from app.platform.services.standard_text_seed import SEED_TEXTS

# revision identifiers, used by Alembic.
revision: str = "ec4559d84ad3"
down_revision: str | Sequence[str] | None = "444ddb84d8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
_CATEGORIES_BEFORE = ("document", "email", "sms", "internal_note", "workshop")
_CATEGORIES = ("document", "email", "sms", "whatsapp", "internal_note", "workshop")
_APPLIES_TO = (
    "offer",
    "contract",
    "invoice",
    "appointment_reminder",
    "vehicle_ready",
    "workshop_order",
    "customer_note",
)
_SLOTS = ("header", "intro", "body", "footer", "terms", "closing")
_SCOPES = ("dealership", "location")
_SOURCES = ("platform_seed", "dealer")
_LANGUAGES = ("de", "fr", "it", "en")

CARRIED_OVER_CODE = "invoice.footer.carried_over"
_CARRIED_OVER_TITLES = {
    "de": "Fusszeile (aus der Dokumentvorlage übernommen)",
    "fr": "Pied de page (repris du modèle de document)",
    "it": "Piè di pagina (ripreso dal modello di documento)",
    "en": "Footer (carried over from the document template)",
}


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


# Where a carried-over footer goes (code, applies_to, is_default). The invoice
# row keeps it for the invoice (not its default: the seeded payment terms stay
# that). The offer and contract rows are the Betrieb's **default** footer of
# those documents, so an offer or contract it prints after the upgrade carries
# the very footer it carried before — the dealer default outranks the seed's.
CARRIED_OVER_TARGETS = (
    (CARRIED_OVER_CODE, "invoice", False),
    ("offer.footer.carried_over", "offer", True),
    ("contract.footer.carried_over", "contract", True),
)


def _carry_over_footers_sql(language: str, code: str, applies_to: str, is_default: bool) -> str:
    title = _CARRIED_OVER_TITLES[language].replace("'", "''")
    column = f"footer_text_{language}"
    default = "true" if is_default else "false"
    return f"""
INSERT INTO standard_text (
    id, dealership_id, location_id, scope, code, category, applies_to, slot, language,
    title, body, source, overridden_from_id, overridden_seed_version,
    is_default, active, sort_order, template_approved, provider_template_id,
    version, created_at, updated_at, created_by, updated_by
)
SELECT gen_random_uuid(), t.dealership_id, NULL, 'dealership', '{code}', 'document', '{applies_to}',
       'footer', '{language}', '{title}', t.{column}, 'dealer', NULL, NULL,
       {default}, true, 100, false, NULL,
       1, now(), now(), t.updated_by, t.updated_by
FROM document_template t
WHERE t.{column} IS NOT NULL AND btrim(t.{column}) <> ''
"""


def upgrade() -> None:
    table = op.create_table(
        "standard_text",
        sa.Column("dealership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("applies_to", sa.String(length=32), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("overridden_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("overridden_seed_version", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("template_approved", sa.Boolean(), nullable=False),
        sa.Column("provider_template_id", sa.String(length=200), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("(scope = 'location') = (location_id IS NOT NULL)", name="ck_standard_text_scope_location"),
        sa.CheckConstraint(
            "(source = 'platform_seed' AND dealership_id IS NULL AND location_id IS NULL "
            "AND overridden_from_id IS NULL) OR (source = 'dealer' AND dealership_id IS NOT NULL)",
            name="ck_standard_text_source_owner",
        ),
        sa.CheckConstraint(_check("applies_to", _APPLIES_TO), name="ck_standard_text_applies_to_enum"),
        sa.CheckConstraint(_check("category", _CATEGORIES), name="ck_standard_text_category_enum"),
        sa.CheckConstraint(_check("language", _LANGUAGES), name="ck_standard_text_language_enum"),
        sa.CheckConstraint(_check("scope", _SCOPES), name="ck_standard_text_scope_enum"),
        sa.CheckConstraint(_check("slot", _SLOTS), name="ck_standard_text_slot_enum"),
        sa.CheckConstraint(_check("source", _SOURCES), name="ck_standard_text_source_enum"),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["overridden_from_id"], ["standard_text.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_standard_text_dealership_id", "standard_text", ["dealership_id"], unique=False)
    op.create_index("ix_standard_text_overridden_from_id", "standard_text", ["overridden_from_id"], unique=False)
    op.create_index(
        "uq_standard_text_dealership_code_language",
        "standard_text",
        ["dealership_id", "code", "language"],
        unique=True,
        postgresql_where=sa.text("dealership_id IS NOT NULL AND location_id IS NULL"),
    )
    op.create_index(
        "uq_standard_text_location_code_language",
        "standard_text",
        ["location_id", "code", "language"],
        unique=True,
        postgresql_where=sa.text("location_id IS NOT NULL"),
    )
    op.create_index(
        "uq_standard_text_seed_code_language",
        "standard_text",
        ["code", "language"],
        unique=True,
        postgresql_where=sa.text("dealership_id IS NULL"),
    )

    now = utcnow()
    op.bulk_insert(
        table,
        [
            {
                "id": uuid7(),
                "dealership_id": None,
                "location_id": None,
                "scope": "dealership",
                "code": seed.code,
                "category": seed.category.value,
                "applies_to": seed.applies_to.value,
                "slot": seed.slot.value,
                "language": language.value,
                "title": seed.titles[language],
                "body": seed.bodies[language],
                "source": "platform_seed",
                "overridden_from_id": None,
                "overridden_seed_version": None,
                "is_default": seed.is_default,
                "active": True,
                "sort_order": seed.sort_order,
                "template_approved": False,
                "provider_template_id": None,
                "version": 1,
                "created_at": now,
                "updated_at": now,
                "created_by": None,
                "updated_by": None,
            }
            for seed in SEED_TEXTS
            for language in seed.titles
        ],
    )

    for code, applies_to, is_default in CARRIED_OVER_TARGETS:
        for language in _LANGUAGES:
            op.execute(_carry_over_footers_sql(language, code, applies_to, is_default))
    for language in _LANGUAGES:
        op.drop_column("document_template", f"footer_text_{language}")

    op.drop_constraint("ck_standard_text_delegation_category_enum", "standard_text_delegation", type_="check")
    op.create_check_constraint(
        "ck_standard_text_delegation_category_enum", "standard_text_delegation", _check("category", _CATEGORIES)
    )


def downgrade() -> None:
    op.execute("DELETE FROM standard_text_delegation WHERE category = 'whatsapp'")
    op.drop_constraint("ck_standard_text_delegation_category_enum", "standard_text_delegation", type_="check")
    op.create_check_constraint(
        "ck_standard_text_delegation_category_enum",
        "standard_text_delegation",
        _check("category", _CATEGORIES_BEFORE),
    )

    for language in _LANGUAGES:
        op.add_column("document_template", sa.Column(f"footer_text_{language}", sa.Text(), nullable=True))
    # A Betrieb whose template row is gone since gets one back to hold its footer.
    op.execute(
        f"""
INSERT INTO document_template (id, dealership_id, version, created_at, updated_at, created_by, updated_by)
SELECT gen_random_uuid(), s.dealership_id, 1, now(), now(), NULL, NULL
FROM (SELECT DISTINCT dealership_id FROM standard_text
      WHERE code = '{CARRIED_OVER_CODE}' AND location_id IS NULL AND dealership_id IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM document_template t WHERE t.dealership_id = s.dealership_id)
"""
    )
    for language in _LANGUAGES:
        op.execute(
            f"""
UPDATE document_template t SET footer_text_{language} = s.body
FROM standard_text s
WHERE s.dealership_id = t.dealership_id AND s.location_id IS NULL
  AND s.code = '{CARRIED_OVER_CODE}' AND s.language = '{language}'
"""
        )

    op.drop_index("uq_standard_text_seed_code_language", table_name="standard_text")
    op.drop_index("uq_standard_text_location_code_language", table_name="standard_text")
    op.drop_index("uq_standard_text_dealership_code_language", table_name="standard_text")
    op.drop_index("ix_standard_text_overridden_from_id", table_name="standard_text")
    op.drop_index("ix_standard_text_dealership_id", table_name="standard_text")
    op.drop_table("standard_text")

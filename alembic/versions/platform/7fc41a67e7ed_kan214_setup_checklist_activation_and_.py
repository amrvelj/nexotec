"""KAN-214 — setup checklist, activation and legal documents

Dealer Administration D-A-21, FR-A-26, FR-A-34, FR-A-39, D-A-34.

Schema:

- `legal_document` / `legal_acceptance` (FR-A-39): Nexotec's ToS and DPA,
  versioned per language, and the append-only record of who accepted which
  version for which Betrieb.
- `number_range_confirmation`: the manager's «Nummernkreise bestätigen» —
  the fact that completes the checklist item «Number ranges confirmed».
- `setup_checklist_regression`: the daily job's ledger of an item broken
  after activation (one notification per regression).
- `dealership.activated_at` / `activated_by`.

Data (rows written out literally, ADR-133):

- **Every Betrieb `pending_onboarding` today becomes `active`.** Until this
  revision nothing read the status — every Betrieb onboarded so far has been
  trading (staging's included) with no checklist and no activation. Gating
  them now would lock their users out of every module overnight, so they are
  treated as activated before the gate existed: `activated_at` stays NULL
  (no activation happened), and the audit log keeps no row for it (a
  migration writes none). From this revision on a new Betrieb starts
  `pending_onboarding` and becomes active only through «Betrieb aktivieren».
  These Betriebe have accepted no ToS or DPA: the re-acceptance rule applies
  to them like to any new version (30 days of prompts, then the manager is
  held at the acceptance screen). Irreversible: the downgrade leaves them
  active.
- The ToS and the DPA as version **0.9 «Entwurf»** in DE, FR, IT and EN,
  effective 2026-11-14, 30 days' grace (Anto, 2026-11-13, Q1 A, Q3 A). Every
  section is a marked draft for counsel (A-5); production refuses activation
  below 1.0, so these texts can activate a Betrieb on staging only.
- The catalogue row `administration.checklist_item_regressed`, the
  administration family's second event (`notification_catalogue.py`; kept
  equal by `tests/test_migrated_notification_catalogue.py`).

Revision ID: 7fc41a67e7ed
Revises: 7d2e5b9c4a18
Create Date: 2026-11-14 14:14:37.399628

"""

import datetime as dt
from collections.abc import Sequence

import sqlalchemy as sa

import app.core.types
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "7fc41a67e7ed"
down_revision: str | Sequence[str] | None = "7d2e5b9c4a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EFFECTIVE = dt.date(2026, 11, 14)
_GRACE_DAYS = 30
_VERSION = "0.9"

_REGRESSED_EVENT = (
    "019a8300-0000-7000-8000-000000000001",
    "administration.checklist_item_regressed",
    "checklistItemRegressed",
)

_DRAFT_NOTE = {
    "de": "> **Entwurf 0.9 — nicht rechtsgültig.** Dieser Text ist ein Platzhalter für die Rechtsberatung (A-5). "
    "Nur auf Staging verwendbar; die Produktion aktiviert erst mit Version 1.0.",
    "fr": "> **Projet 0.9 — sans valeur juridique.** Ce texte est un espace réservé pour le conseil juridique (A-5). "
    "Utilisable uniquement en préproduction ; la production n'active qu'avec la version 1.0.",
    "it": "> **Bozza 0.9 — senza valore legale.** Questo testo è un segnaposto per la consulenza legale (A-5). "
    "Utilizzabile solo in staging; la produzione attiva solo con la versione 1.0.",
    "en": "> **Draft 0.9 — not legally binding.** This text is a placeholder for counsel (A-5). "
    "Usable on staging only; production activates with version 1.0 only.",
}

_TOS = {
    "de": (
        "Nutzungsbedingungen Nexotec DMS (Entwurf)",
        [
            ("1. Gegenstand", "Platzhalter: Leistungsbeschreibung des Nexotec DMS."),
            (
                "2. Zugang und Benutzer",
                "Platzhalter: Konten, Rollen und Verantwortung des Betriebs für seine Benutzer.",
            ),
            ("3. Daten des Betriebs", "Platzhalter: Eigentum an den Daten, Export und Offboarding (D-A-31)."),
            ("4. Verfügbarkeit und Support", "Platzhalter: Betriebszeiten, Wartung, Support-Zugang."),
            ("5. Preise und Laufzeit", "Platzhalter: Vergütung, Laufzeit und Kündigung."),
            ("6. Haftung", "Platzhalter: Haftungsbeschränkung."),
            ("7. Anwendbares Recht", "Platzhalter: Schweizer Recht, Gerichtsstand."),
        ],
    ),
    "fr": (
        "Conditions d'utilisation Nexotec DMS (projet)",
        [
            ("1. Objet", "Espace réservé : description des prestations du Nexotec DMS."),
            (
                "2. Accès et utilisateurs",
                "Espace réservé : comptes, rôles et responsabilité de l'entreprise pour ses utilisateurs.",
            ),
            ("3. Données de l'entreprise", "Espace réservé : propriété des données, export et sortie (D-A-31)."),
            ("4. Disponibilité et support", "Espace réservé : heures d'exploitation, maintenance, accès du support."),
            ("5. Prix et durée", "Espace réservé : rémunération, durée et résiliation."),
            ("6. Responsabilité", "Espace réservé : limitation de responsabilité."),
            ("7. Droit applicable", "Espace réservé : droit suisse, for juridique."),
        ],
    ),
    "it": (
        "Condizioni d'uso Nexotec DMS (bozza)",
        [
            ("1. Oggetto", "Segnaposto: descrizione delle prestazioni del Nexotec DMS."),
            ("2. Accesso e utenti", "Segnaposto: account, ruoli e responsabilità dell'azienda per i suoi utenti."),
            ("3. Dati dell'azienda", "Segnaposto: proprietà dei dati, esportazione e uscita (D-A-31)."),
            ("4. Disponibilità e supporto", "Segnaposto: orari di esercizio, manutenzione, accesso del supporto."),
            ("5. Prezzi e durata", "Segnaposto: compenso, durata e disdetta."),
            ("6. Responsabilità", "Segnaposto: limitazione della responsabilità."),
            ("7. Diritto applicabile", "Segnaposto: diritto svizzero, foro competente."),
        ],
    ),
    "en": (
        "Nexotec DMS Terms of Service (draft)",
        [
            ("1. Subject", "Placeholder: description of the Nexotec DMS service."),
            ("2. Access and users", "Placeholder: accounts, roles and the business's responsibility for its users."),
            ("3. The business's data", "Placeholder: ownership of the data, export and offboarding (D-A-31)."),
            ("4. Availability and support", "Placeholder: operating hours, maintenance, support access."),
            ("5. Prices and term", "Placeholder: fees, term and termination."),
            ("6. Liability", "Placeholder: limitation of liability."),
            ("7. Governing law", "Placeholder: Swiss law, place of jurisdiction."),
        ],
    ),
}

_DPA = {
    "de": (
        "Auftragsverarbeitungsvertrag Nexotec DMS (Entwurf)",
        [
            (
                "1. Gegenstand und Dauer",
                "Platzhalter: Nexotec bearbeitet Personendaten im Auftrag des Betriebs, solange der Vertrag läuft.",
            ),
            (
                "2. Art der Daten und betroffene Personen",
                "Platzhalter: Kunden, Lieferanten, Mitarbeitende des Betriebs.",
            ),
            (
                "3. Technische und organisatorische Massnahmen",
                "Platzhalter: Verschlüsselung, Zugriffskontrolle, Protokollierung.",
            ),
            ("4. Unterauftragsverarbeiter", "Platzhalter: Liste und Genehmigungsverfahren."),
            (
                "5. Support-Zugang",
                (
                    "Entwurf: Nexotec-Mitarbeitende dürfen zur Unterstützung in der Sitzung eines Benutzers arbeiten. "
                    "Jede solche Sitzung braucht einen Grund, dauert höchstens 60 Minuten, wird dem Benutzer angezeigt, "
                    "kann von ihm beendet werden und wird mit beiden Identitäten protokolliert (A-1)."
                ),
            ),
            (
                "6. Rechte der betroffenen Personen",
                "Platzhalter: Unterstützung bei Auskunft, Berichtigung, Löschung (revDSG).",
            ),
            ("7. Rückgabe und Löschung", "Platzhalter: Export und zehnjähriges Archiv nach dem Offboarding (D-A-31)."),
        ],
    ),
    "fr": (
        "Contrat de sous-traitance Nexotec DMS (projet)",
        [
            (
                "1. Objet et durée",
                "Espace réservé : Nexotec traite des données personnelles pour le compte de l'entreprise pendant la durée du contrat.",
            ),
            (
                "2. Nature des données et personnes concernées",
                "Espace réservé : clients, fournisseurs, collaborateurs de l'entreprise.",
            ),
            (
                "3. Mesures techniques et organisationnelles",
                "Espace réservé : chiffrement, contrôle d'accès, journalisation.",
            ),
            ("4. Sous-traitants ultérieurs", "Espace réservé : liste et procédure d'autorisation."),
            (
                "5. Accès du support",
                (
                    "Projet : les collaborateurs de Nexotec peuvent travailler dans la session d'un utilisateur pour l'assister. "
                    "Chaque session de ce type exige un motif, dure au plus 60 minutes, est signalée à l'utilisateur, "
                    "peut être terminée par lui et est journalisée avec les deux identités (A-1)."
                ),
            ),
            (
                "6. Droits des personnes concernées",
                "Espace réservé : assistance pour l'accès, la rectification et l'effacement (nLPD).",
            ),
            ("7. Restitution et effacement", "Espace réservé : export et archive de dix ans après la sortie (D-A-31)."),
        ],
    ),
    "it": (
        "Contratto di trattamento dei dati Nexotec DMS (bozza)",
        [
            (
                "1. Oggetto e durata",
                "Segnaposto: Nexotec tratta dati personali per conto dell'azienda per la durata del contratto.",
            ),
            ("2. Natura dei dati e persone interessate", "Segnaposto: clienti, fornitori, collaboratori dell'azienda."),
            ("3. Misure tecniche e organizzative", "Segnaposto: cifratura, controllo degli accessi, registrazione."),
            ("4. Sub-responsabili", "Segnaposto: elenco e procedura di autorizzazione."),
            (
                "5. Accesso del supporto",
                (
                    "Bozza: i collaboratori di Nexotec possono lavorare nella sessione di un utente per assisterlo. "
                    "Ogni sessione di questo tipo richiede un motivo, dura al massimo 60 minuti, è mostrata all'utente, "
                    "può essere terminata da lui ed è registrata con entrambe le identità (A-1)."
                ),
            ),
            (
                "6. Diritti delle persone interessate",
                "Segnaposto: supporto per accesso, rettifica e cancellazione (nLPD).",
            ),
            (
                "7. Restituzione e cancellazione",
                "Segnaposto: esportazione e archivio di dieci anni dopo l'uscita (D-A-31).",
            ),
        ],
    ),
    "en": (
        "Nexotec DMS Data Processing Agreement (draft)",
        [
            (
                "1. Subject and term",
                "Placeholder: Nexotec processes personal data on the business's behalf for the term of the contract.",
            ),
            ("2. Kinds of data and data subjects", "Placeholder: the business's customers, suppliers and staff."),
            ("3. Technical and organisational measures", "Placeholder: encryption, access control, logging."),
            ("4. Sub-processors", "Placeholder: list and approval procedure."),
            (
                "5. Support access",
                (
                    "Draft: Nexotec staff may work in a user's session to support them. Every such session needs a "
                    "reason, lasts at most 60 minutes, is shown to the user, can be ended by them and is logged with "
                    "both identities (A-1)."
                ),
            ),
            ("6. Data subjects' rights", "Placeholder: support with access, rectification and erasure (revFADP)."),
            ("7. Return and deletion", "Placeholder: export and the ten-year archive after offboarding (D-A-31)."),
        ],
    ),
}

# Fixed ids, so every environment holds the same rows.
_DOCUMENT_IDS = {
    ("tos", "de"): "019a8300-0000-7000-8000-000000000101",
    ("tos", "fr"): "019a8300-0000-7000-8000-000000000102",
    ("tos", "it"): "019a8300-0000-7000-8000-000000000103",
    ("tos", "en"): "019a8300-0000-7000-8000-000000000104",
    ("dpa", "de"): "019a8300-0000-7000-8000-000000000201",
    ("dpa", "fr"): "019a8300-0000-7000-8000-000000000202",
    ("dpa", "it"): "019a8300-0000-7000-8000-000000000203",
    ("dpa", "en"): "019a8300-0000-7000-8000-000000000204",
}


def _body(language: str, sections: list[tuple[str, str]]) -> str:
    parts = [_DRAFT_NOTE[language]]
    parts += [f"## {heading}\n\n{text}" for heading, text in sections]
    return "\n\n".join(parts) + "\n"


def _documents() -> list[dict]:
    rows = []
    for document_type, texts in (("tos", _TOS), ("dpa", _DPA)):
        for language, (title, sections) in texts.items():
            rows.append(
                {
                    "id": _DOCUMENT_IDS[(document_type, language)],
                    "document_type": document_type,
                    "version": _VERSION,
                    "language": language,
                    "effective_date": _EFFECTIVE,
                    "title": title,
                    "body_markdown": _body(language, sections),
                    "acceptance_grace_days": _GRACE_DAYS,
                    "published_at": dt.datetime(2026, 11, 14, tzinfo=dt.UTC),
                    "published_by": None,
                }
            )
    return rows


def upgrade() -> None:
    legal_document = op.create_table(
        "legal_document",
        sa.Column("document_type", sa.String(length=8), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("acceptance_grace_days", sa.Integer(), nullable=False),
        sa.Column("published_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column(
            "published_by",
            app.core.types.GUID(),
            nullable=True,
            comment="The staff user who published it (platform); NULL for a migration seed.",
        ),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.CheckConstraint("document_type IN ('tos', 'dpa')", name="ck_legal_document_document_type_enum"),
        sa.CheckConstraint("language IN ('de', 'fr', 'it', 'en')", name="ck_legal_document_language_enum"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_type", "version", "language", name="uq_legal_document_document_type_version_language"
        ),
    )
    op.create_table(
        "legal_acceptance",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("legal_document_id", app.core.types.GUID(), nullable=False),
        sa.Column("document_type", sa.String(length=8), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("user_id", app.core.types.GUID(), nullable=False, comment="The accepting manager (platform user)."),
        sa.Column("accepted_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.CheckConstraint("document_type IN ('tos', 'dpa')", name="ck_legal_acceptance_document_type_enum"),
        sa.CheckConstraint("language IN ('de', 'fr', 'it', 'en')", name="ck_legal_acceptance_language_enum"),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.ForeignKeyConstraint(["legal_document_id"], ["legal_document.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_acceptance_dealership_id_document_type",
        "legal_acceptance",
        ["dealership_id", "document_type"],
        unique=False,
    )
    op.create_table(
        "number_range_confirmation",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("confirmed_by", app.core.types.GUID(), nullable=False, comment="The confirming user (platform)."),
        sa.Column("confirmed_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_number_range_confirmation_dealership_id", "number_range_confirmation", ["dealership_id"], unique=False
    )
    op.create_table(
        "setup_checklist_regression",
        sa.Column("dealership_id", app.core.types.GUID(), nullable=False),
        sa.Column("item_code", sa.String(length=32), nullable=False),
        sa.Column("detected_at", app.core.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", app.core.types.UTCDateTime(timezone=True), nullable=True),
        sa.Column("id", app.core.types.GUID(), nullable=False),
        sa.ForeignKeyConstraint(["dealership_id"], ["dealership.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_setup_checklist_regression_open",
        "setup_checklist_regression",
        ["dealership_id", "item_code"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )
    op.add_column("dealership", sa.Column("activated_at", app.core.types.UTCDateTime(timezone=True), nullable=True))
    op.add_column(
        "dealership",
        sa.Column(
            "activated_by",
            app.core.types.GUID(),
            nullable=True,
            comment="The manager who activated the Betrieb (platform user).",
        ),
    )

    # The Betriebe already trading stay reachable (see the module docstring).
    if not context.is_offline_mode():
        pending = (
            op.get_bind()
            .execute(sa.text("SELECT count(*) FROM dealership WHERE status = 'pending_onboarding'"))
            .scalar_one()
        )
        print(f"KAN-214: {pending} Betrieb(e) pending_onboarding set active (trading before the gate existed)")
    op.execute("UPDATE dealership SET status = 'active' WHERE status = 'pending_onboarding'")

    op.bulk_insert(legal_document, _documents())

    notification_event = sa.table(
        "notification_event",
        sa.column("id", app.core.types.GUID()),
        sa.column("code", sa.String()),
        sa.column("family", sa.String()),
        sa.column("default_timing", sa.String()),
        sa.column("switchable", sa.Boolean()),
        sa.column("title_key", sa.String()),
        sa.column("body_key", sa.String()),
    )
    event_id, code, stem = _REGRESSED_EVENT
    op.bulk_insert(
        notification_event,
        [
            {
                "id": event_id,
                "code": code,
                "family": "administration",
                "default_timing": "immediate",
                "switchable": True,
                "title_key": f"notifications.events.{stem}.title",
                "body_key": f"notifications.events.{stem}.body",
            }
        ],
    )


def downgrade() -> None:
    # The Betriebe set active stay active: which ones were pending is not kept.
    op.execute(sa.text("DELETE FROM notification WHERE event_code = :code").bindparams(code=_REGRESSED_EVENT[1]))
    op.execute(sa.text("DELETE FROM notification_event WHERE code = :code").bindparams(code=_REGRESSED_EVENT[1]))
    op.drop_column("dealership", "activated_by")
    op.drop_column("dealership", "activated_at")
    op.drop_index(
        "uq_setup_checklist_regression_open",
        table_name="setup_checklist_regression",
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_table("setup_checklist_regression")
    op.drop_index("ix_number_range_confirmation_dealership_id", table_name="number_range_confirmation")
    op.drop_table("number_range_confirmation")
    op.drop_index("ix_legal_acceptance_dealership_id_document_type", table_name="legal_acceptance")
    op.drop_table("legal_acceptance")
    op.drop_table("legal_document")

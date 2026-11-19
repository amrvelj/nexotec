"""KAN-217 PR-1: the send dialog's message texts, and «order confirmation» as
a place a standard text is used.

- `standard_text.applies_to` gains `order_confirmation` (Sales' order
  confirmation, a registered message type, FR-A-52). The CHECK is replaced
  with the full list (tests/test_migrated_enum_checks.py).
- Eight platform seeds × four languages: the SMS and WhatsApp bodies of the
  offer, and the e-mail, SMS and WhatsApp bodies of the order confirmation and
  the sales contract — the second edition of the seed library
  (`app/platform/documents/services/message_text_seed.py`), written out here
  as literals (ADR-133) and kept equal to it by
  tests/platform/test_kan217_message_types.py.

Downgrade refuses while a Betrieb holds a text of its own for an order
confirmation or an override of one of these seeds — deleting it would lose the
dealer's wording; then it deletes the seeds and restores the CHECK.

Revision ID: a4d9e6c3b7f2
Revises: c2a7e5d9f4b1
Create Date: 2026-11-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op
from app.core.base import utcnow
from app.core.uuid7 import uuid7

# revision identifiers, used by Alembic.
revision: str = "a4d9e6c3b7f2"
down_revision: str | Sequence[str] | None = "c2a7e5d9f4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen at this revision, in enum order (enum_check_condition's wording).
_APPLIES_TO_BEFORE = (
    "offer",
    "contract",
    "invoice",
    "appointment_reminder",
    "vehicle_ready",
    "workshop_order",
    "customer_note",
)
_APPLIES_TO = (
    "offer",
    "contract",
    "order_confirmation",
    "invoice",
    "appointment_reminder",
    "vehicle_ready",
    "workshop_order",
    "customer_note",
)

# (code, category, applies_to, slot, sort_order, language, title, body)
SEED_ROWS: tuple[tuple[str, str, str, str, int, str, str, str], ...] = (
    (
        "sms.offer_sent",
        "sms",
        "offer",
        "body",
        10,
        "de",
        "Offerte versandt",
        "{{customer.salutation}}\nIhre Offerte {{document.number}} für {{vehicle.make_model}} ist bereit. Sie finden sie über den Link in dieser Nachricht.\nFreundliche Grüsse\n{{dealership.name}}",
    ),
    (
        "sms.offer_sent",
        "sms",
        "offer",
        "body",
        10,
        "fr",
        "Envoi de l'offre",
        "{{customer.salutation}},\nvotre offre {{document.number}} pour le véhicule {{vehicle.make_model}} est prête. Vous la trouverez via le lien figurant dans ce message.\nMeilleures salutations\n{{dealership.name}}",
    ),
    (
        "sms.offer_sent",
        "sms",
        "offer",
        "body",
        10,
        "it",
        "Invio dell'offerta",
        "{{customer.salutation}},\nla Sua offerta {{document.number}} per il veicolo {{vehicle.make_model}} è pronta. La trova tramite il link contenuto in questo messaggio.\nCordiali saluti\n{{dealership.name}}",
    ),
    (
        "sms.offer_sent",
        "sms",
        "offer",
        "body",
        10,
        "en",
        "Offer sent",
        "{{customer.salutation}},\nyour offer {{document.number}} for the {{vehicle.make_model}} is ready. You can find it through the link in this message.\nKind regards\n{{dealership.name}}",
    ),
    (
        "whatsapp.offer_sent",
        "whatsapp",
        "offer",
        "body",
        10,
        "de",
        "Offerte versandt",
        "{{customer.salutation}}\n\nVielen Dank für Ihr Interesse. Ihre Offerte {{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\n\nFür Fragen stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.offer_sent",
        "whatsapp",
        "offer",
        "body",
        10,
        "fr",
        "Envoi de l'offre",
        "{{customer.salutation}},\n\nNous vous remercions de votre intérêt. Vous trouverez votre offre {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\n\nJe reste volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.offer_sent",
        "whatsapp",
        "offer",
        "body",
        10,
        "it",
        "Invio dell'offerta",
        "{{customer.salutation}},\n\nla ringraziamo per il Suo interesse. Trova la Sua offerta {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\n\nResto volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.offer_sent",
        "whatsapp",
        "offer",
        "body",
        10,
        "en",
        "Offer sent",
        "{{customer.salutation}},\n\nThank you for your interest. You can find your offer {{document.number}} of {{document.date}} for the {{vehicle.make_model}} through the link in this message.\n\nI will be glad to answer any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.order_confirmation_sent",
        "email",
        "order_confirmation",
        "body",
        10,
        "de",
        "Auftragsbestätigung versandt",
        "{{customer.salutation}}\n\nVielen Dank für Ihre Bestellung. Im Anhang finden Sie unsere Auftragsbestätigung {{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}}.\n\nFür Fragen stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.order_confirmation_sent",
        "email",
        "order_confirmation",
        "body",
        10,
        "fr",
        "Envoi de la confirmation de commande",
        "{{customer.salutation}},\n\nNous vous remercions de votre commande. Vous trouverez en annexe notre confirmation de commande {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}}.\n\nJe reste volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.order_confirmation_sent",
        "email",
        "order_confirmation",
        "body",
        10,
        "it",
        "Invio della conferma d'ordine",
        "{{customer.salutation}},\n\nla ringraziamo per il Suo ordine. In allegato trova la nostra conferma d'ordine {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}}.\n\nResto volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.order_confirmation_sent",
        "email",
        "order_confirmation",
        "body",
        10,
        "en",
        "Order confirmation sent",
        "{{customer.salutation}},\n\nThank you for your order. Please find attached our order confirmation {{document.number}} of {{document.date}} for the {{vehicle.make_model}}.\n\nI will be glad to answer any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "sms.order_confirmation_sent",
        "sms",
        "order_confirmation",
        "body",
        10,
        "de",
        "Auftragsbestätigung versandt",
        "{{customer.salutation}}\nVielen Dank für Ihre Bestellung. Die Auftragsbestätigung {{document.number}} für {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\nFreundliche Grüsse\n{{dealership.name}}",
    ),
    (
        "sms.order_confirmation_sent",
        "sms",
        "order_confirmation",
        "body",
        10,
        "fr",
        "Envoi de la confirmation de commande",
        "{{customer.salutation}},\nmerci de votre commande. Vous trouverez la confirmation de commande {{document.number}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\nMeilleures salutations\n{{dealership.name}}",
    ),
    (
        "sms.order_confirmation_sent",
        "sms",
        "order_confirmation",
        "body",
        10,
        "it",
        "Invio della conferma d'ordine",
        "{{customer.salutation}},\ngrazie per il Suo ordine. Trova la conferma d'ordine {{document.number}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\nCordiali saluti\n{{dealership.name}}",
    ),
    (
        "sms.order_confirmation_sent",
        "sms",
        "order_confirmation",
        "body",
        10,
        "en",
        "Order confirmation sent",
        "{{customer.salutation}},\nthank you for your order. You can find the order confirmation {{document.number}} for the {{vehicle.make_model}} through the link in this message.\nKind regards\n{{dealership.name}}",
    ),
    (
        "whatsapp.order_confirmation_sent",
        "whatsapp",
        "order_confirmation",
        "body",
        10,
        "de",
        "Auftragsbestätigung versandt",
        "{{customer.salutation}}\n\nVielen Dank für Ihre Bestellung. Die Auftragsbestätigung {{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\n\nFür Fragen stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.order_confirmation_sent",
        "whatsapp",
        "order_confirmation",
        "body",
        10,
        "fr",
        "Envoi de la confirmation de commande",
        "{{customer.salutation}},\n\nNous vous remercions de votre commande. Vous trouverez la confirmation de commande {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\n\nJe reste volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.order_confirmation_sent",
        "whatsapp",
        "order_confirmation",
        "body",
        10,
        "it",
        "Invio della conferma d'ordine",
        "{{customer.salutation}},\n\nla ringraziamo per il Suo ordine. Trova la conferma d'ordine {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\n\nResto volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.order_confirmation_sent",
        "whatsapp",
        "order_confirmation",
        "body",
        10,
        "en",
        "Order confirmation sent",
        "{{customer.salutation}},\n\nThank you for your order. You can find the order confirmation {{document.number}} of {{document.date}} for the {{vehicle.make_model}} through the link in this message.\n\nI will be glad to answer any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.contract_sent",
        "email",
        "contract",
        "body",
        10,
        "de",
        "Kaufvertrag versandt",
        "{{customer.salutation}}\n\nIm Anhang finden Sie den Kaufvertrag {{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}}.\n\nVielen Dank für Ihr Vertrauen. Für Fragen stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.contract_sent",
        "email",
        "contract",
        "body",
        10,
        "fr",
        "Envoi du contrat de vente",
        "{{customer.salutation}},\n\nVous trouverez en annexe le contrat de vente {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}}.\n\nNous vous remercions de votre confiance. Je reste volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.contract_sent",
        "email",
        "contract",
        "body",
        10,
        "it",
        "Invio del contratto di vendita",
        "{{customer.salutation}},\n\nin allegato trova il contratto di vendita {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}}.\n\nLa ringraziamo per la fiducia. Resto volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "email.contract_sent",
        "email",
        "contract",
        "body",
        10,
        "en",
        "Purchase contract sent",
        "{{customer.salutation}},\n\nPlease find attached the purchase contract {{document.number}} of {{document.date}} for the {{vehicle.make_model}}.\n\nThank you for your trust. I will be glad to answer any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "sms.contract_sent",
        "sms",
        "contract",
        "body",
        10,
        "de",
        "Kaufvertrag versandt",
        "{{customer.salutation}}\nDen Kaufvertrag {{document.number}} für {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\nFreundliche Grüsse\n{{dealership.name}}",
    ),
    (
        "sms.contract_sent",
        "sms",
        "contract",
        "body",
        10,
        "fr",
        "Envoi du contrat de vente",
        "{{customer.salutation}},\nvous trouverez le contrat de vente {{document.number}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\nMeilleures salutations\n{{dealership.name}}",
    ),
    (
        "sms.contract_sent",
        "sms",
        "contract",
        "body",
        10,
        "it",
        "Invio del contratto di vendita",
        "{{customer.salutation}},\ntrova il contratto di vendita {{document.number}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\nCordiali saluti\n{{dealership.name}}",
    ),
    (
        "sms.contract_sent",
        "sms",
        "contract",
        "body",
        10,
        "en",
        "Purchase contract sent",
        "{{customer.salutation}},\nyou can find the purchase contract {{document.number}} for the {{vehicle.make_model}} through the link in this message.\nKind regards\n{{dealership.name}}",
    ),
    (
        "whatsapp.contract_sent",
        "whatsapp",
        "contract",
        "body",
        10,
        "de",
        "Kaufvertrag versandt",
        "{{customer.salutation}}\n\nDen Kaufvertrag {{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\n\nVielen Dank für Ihr Vertrauen. Für Fragen stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.contract_sent",
        "whatsapp",
        "contract",
        "body",
        10,
        "fr",
        "Envoi du contrat de vente",
        "{{customer.salutation}},\n\nVous trouverez le contrat de vente {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\n\nNous vous remercions de votre confiance. Je reste volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.contract_sent",
        "whatsapp",
        "contract",
        "body",
        10,
        "it",
        "Invio del contratto di vendita",
        "{{customer.salutation}},\n\ntrova il contratto di vendita {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\n\nLa ringraziamo per la fiducia. Resto volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n{{dealership.name}}",
    ),
    (
        "whatsapp.contract_sent",
        "whatsapp",
        "contract",
        "body",
        10,
        "en",
        "Purchase contract sent",
        "{{customer.salutation}},\n\nYou can find the purchase contract {{document.number}} of {{document.date}} for the {{vehicle.make_model}} through the link in this message.\n\nThank you for your trust. I will be glad to answer any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
    ),
)
SEED_CODES = tuple(dict.fromkeys(row[0] for row in SEED_ROWS))


def _check(column: str, values: tuple[str, ...]) -> str:
    return f'"{column}" IN (' + ", ".join(f"'{v}'" for v in values) + ")"


def _replace_applies_to(values: tuple[str, ...]) -> None:
    op.drop_constraint("ck_standard_text_applies_to_enum", "standard_text", type_="check")
    op.create_check_constraint("ck_standard_text_applies_to_enum", "standard_text", _check("applies_to", values))


_standard_text = sa.table(
    "standard_text",
    sa.column("id", sa.Uuid()),
    sa.column("dealership_id", sa.Uuid()),
    sa.column("location_id", sa.Uuid()),
    sa.column("scope", sa.String()),
    sa.column("code", sa.String()),
    sa.column("category", sa.String()),
    sa.column("applies_to", sa.String()),
    sa.column("slot", sa.String()),
    sa.column("language", sa.String()),
    sa.column("title", sa.String()),
    sa.column("body", sa.Text()),
    sa.column("source", sa.String()),
    sa.column("overridden_from_id", sa.Uuid()),
    sa.column("overridden_seed_version", sa.Integer()),
    sa.column("is_default", sa.Boolean()),
    sa.column("active", sa.Boolean()),
    sa.column("sort_order", sa.Integer()),
    sa.column("template_approved", sa.Boolean()),
    sa.column("provider_template_id", sa.String()),
    sa.column("version", sa.Integer()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
    sa.column("created_by", sa.Uuid()),
    sa.column("updated_by", sa.Uuid()),
)


def upgrade() -> None:
    _replace_applies_to(_APPLIES_TO)
    now = utcnow()
    op.bulk_insert(
        _standard_text,
        [
            {
                "id": uuid7(),
                "dealership_id": None,
                "location_id": None,
                "scope": "dealership",
                "code": code,
                "category": category,
                "applies_to": applies_to,
                "slot": slot,
                "language": language,
                "title": title,
                "body": body,
                "source": "platform_seed",
                "overridden_from_id": None,
                "overridden_seed_version": None,
                "is_default": True,
                "active": True,
                "sort_order": sort_order,
                "template_approved": False,
                "provider_template_id": None,
                "version": 1,
                "created_at": now,
                "updated_at": now,
                "created_by": None,
                "updated_by": None,
            }
            for code, category, applies_to, slot, sort_order, language, title, body in SEED_ROWS
        ],
    )


def downgrade() -> None:
    codes = ", ".join(f"'{code}'" for code in SEED_CODES)
    if context.is_offline_mode():
        op.execute("-- the check for dealer texts on these seeds skipped: it reads rows and needs a live database")
    else:
        held = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT count(*) FROM standard_text WHERE dealership_id IS NOT NULL AND "
                    f"(applies_to = 'order_confirmation' OR code IN ({codes}))"
                )
            )
            .scalar_one()
        )
        if held:
            raise RuntimeError(
                f"{held} dealer standard text(s) use the KAN-217 message texts or the order confirmation; "
                "delete them first (they are the dealer's wording)."
            )
    op.execute(f"DELETE FROM standard_text WHERE dealership_id IS NULL AND code IN ({codes})")
    _replace_applies_to(_APPLIES_TO_BEFORE)

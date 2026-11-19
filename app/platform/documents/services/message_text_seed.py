"""The message texts of the send dialog's first registered types (KAN-217
PR-1; Dealer Administration FR-A-52): for each of Sales' offer, order
confirmation and sales contract, one body per channel — e-mail, SMS,
WhatsApp — in all four languages.

The second edition of the seed library (ADR-133): `standard_text_seed.py`
is frozen with the KAN-208 migration that imports it, so these rows ship in a
migration of their own (`a4d9e6c3b7f2`, as literals), and
`tests/platform/test_kan217_message_types.py` keeps the two equal. The offer's
e-mail body (`email.offer_sent`) shipped with the first edition and is not
repeated here.

- **The same rules as the first edition:** templates, not legal advice
  (FR-A-08, «Vorlage — bitte prüfen»); Swiss German spelling, «ss» never «ß»;
  the generic masculine (A-RULE-8).
- **An SMS or WhatsApp text names no link.** Those channels carry no
  attachment; the send service appends the secure link to the document on a
  line of its own after the text (FR-A-52), so no edit of a text can lose it
  and no ninth placeholder is needed. The texts say the link is in the
  message.
- **WhatsApp seeds are not provider-approved** (`template_approved` false):
  approval is per Betrieb and its provider account (FR-A-54), so a Betrieb's
  own approved copy carries the template id.

The code's shape is `<category>.<type>` (the first edition's rule for a
message body).
"""

from app.core.i18n import SwissLanguage
from app.platform.documents.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.identity.models.standard_text_delegation import StandardTextCategory
from app.platform.services.standard_text_seed import SeedText

_DE, _FR, _IT, _EN = SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN
_MAIL = StandardTextCategory.EMAIL
_SMS = StandardTextCategory.SMS
_WA = StandardTextCategory.WHATSAPP
_A = StandardTextAppliesTo
_BODY = StandardTextSlot.BODY

_SIGN_DE = "Freundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}"
_SIGN_FR = "Meilleures salutations\n{{user.full_name}}\n{{dealership.name}}"
_SIGN_IT = "Cordiali saluti\n{{user.full_name}}\n{{dealership.name}}"
_SIGN_EN = "Kind regards\n{{user.full_name}}\n{{dealership.name}}"

_QUESTIONS_DE = "Für Fragen stehe ich Ihnen gerne zur Verfügung."
_QUESTIONS_FR = "Je reste volontiers à votre disposition pour toute question."
_QUESTIONS_IT = "Resto volentieri a Sua disposizione per qualsiasi domanda."
_QUESTIONS_EN = "I will be glad to answer any questions."

MESSAGE_SEED_TEXTS: tuple[SeedText, ...] = (
    # --- the offer (its e-mail body is the first edition's email.offer_sent) ---
    SeedText(
        code="sms.offer_sent",
        category=_SMS,
        applies_to=_A.OFFER,
        slot=_BODY,
        sort_order=10,
        titles={_DE: "Offerte versandt", _FR: "Envoi de l'offre", _IT: "Invio dell'offerta", _EN: "Offer sent"},
        bodies={
            _DE: "{{customer.salutation}}\nIhre Offerte {{document.number}} für {{vehicle.make_model}} ist bereit. "
            "Sie finden sie über den Link in dieser Nachricht.\nFreundliche Grüsse\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\nvotre offre {{document.number}} pour le véhicule {{vehicle.make_model}} "
            "est prête. Vous la trouverez via le lien figurant dans ce message.\nMeilleures salutations\n"
            "{{dealership.name}}",
            _IT: "{{customer.salutation}},\nla Sua offerta {{document.number}} per il veicolo {{vehicle.make_model}} "
            "è pronta. La trova tramite il link contenuto in questo messaggio.\nCordiali saluti\n{{dealership.name}}",
            _EN: "{{customer.salutation}},\nyour offer {{document.number}} for the {{vehicle.make_model}} is ready. "
            "You can find it through the link in this message.\nKind regards\n{{dealership.name}}",
        },
    ),
    SeedText(
        code="whatsapp.offer_sent",
        category=_WA,
        applies_to=_A.OFFER,
        slot=_BODY,
        sort_order=10,
        titles={_DE: "Offerte versandt", _FR: "Envoi de l'offre", _IT: "Invio dell'offerta", _EN: "Offer sent"},
        bodies={
            _DE: "{{customer.salutation}}\n\nVielen Dank für Ihr Interesse. Ihre Offerte {{document.number}} vom "
            "{{document.date}} für das Fahrzeug {{vehicle.make_model}} finden Sie über den Link in dieser "
            f"Nachricht.\n\n{_QUESTIONS_DE}\n\n{_SIGN_DE}",
            _FR: "{{customer.salutation}},\n\nNous vous remercions de votre intérêt. Vous trouverez votre offre "
            "{{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}} via le lien figurant "
            f"dans ce message.\n\n{_QUESTIONS_FR}\n\n{_SIGN_FR}",
            _IT: "{{customer.salutation}},\n\nla ringraziamo per il Suo interesse. Trova la Sua offerta "
            "{{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}} tramite il link "
            f"contenuto in questo messaggio.\n\n{_QUESTIONS_IT}\n\n{_SIGN_IT}",
            _EN: "{{customer.salutation}},\n\nThank you for your interest. You can find your offer "
            "{{document.number}} of {{document.date}} for the {{vehicle.make_model}} through the link in this "
            f"message.\n\n{_QUESTIONS_EN}\n\n{_SIGN_EN}",
        },
    ),
    # --- the order confirmation ---
    SeedText(
        code="email.order_confirmation_sent",
        category=_MAIL,
        applies_to=_A.ORDER_CONFIRMATION,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Auftragsbestätigung versandt",
            _FR: "Envoi de la confirmation de commande",
            _IT: "Invio della conferma d'ordine",
            _EN: "Order confirmation sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nVielen Dank für Ihre Bestellung. Im Anhang finden Sie unsere "
            "Auftragsbestätigung {{document.number}} vom {{document.date}} für das Fahrzeug "
            f"{{{{vehicle.make_model}}}}.\n\n{_QUESTIONS_DE}\n\n{_SIGN_DE}",
            _FR: "{{customer.salutation}},\n\nNous vous remercions de votre commande. Vous trouverez en annexe notre "
            "confirmation de commande {{document.number}} du {{document.date}} pour le véhicule "
            f"{{{{vehicle.make_model}}}}.\n\n{_QUESTIONS_FR}\n\n{_SIGN_FR}",
            _IT: "{{customer.salutation}},\n\nla ringraziamo per il Suo ordine. In allegato trova la nostra conferma "
            "d'ordine {{document.number}} del {{document.date}} per il veicolo "
            f"{{{{vehicle.make_model}}}}.\n\n{_QUESTIONS_IT}\n\n{_SIGN_IT}",
            _EN: "{{customer.salutation}},\n\nThank you for your order. Please find attached our order confirmation "
            f"{{{{document.number}}}} of {{{{document.date}}}} for the {{{{vehicle.make_model}}}}.\n\n"
            f"{_QUESTIONS_EN}\n\n{_SIGN_EN}",
        },
    ),
    SeedText(
        code="sms.order_confirmation_sent",
        category=_SMS,
        applies_to=_A.ORDER_CONFIRMATION,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Auftragsbestätigung versandt",
            _FR: "Envoi de la confirmation de commande",
            _IT: "Invio della conferma d'ordine",
            _EN: "Order confirmation sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\nVielen Dank für Ihre Bestellung. Die Auftragsbestätigung "
            "{{document.number}} für {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\n"
            "Freundliche Grüsse\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\nmerci de votre commande. Vous trouverez la confirmation de commande "
            "{{document.number}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\n"
            "Meilleures salutations\n{{dealership.name}}",
            _IT: "{{customer.salutation}},\ngrazie per il Suo ordine. Trova la conferma d'ordine {{document.number}} "
            "per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo messaggio.\nCordiali saluti\n"
            "{{dealership.name}}",
            _EN: "{{customer.salutation}},\nthank you for your order. You can find the order confirmation "
            "{{document.number}} for the {{vehicle.make_model}} through the link in this message.\nKind regards\n"
            "{{dealership.name}}",
        },
    ),
    SeedText(
        code="whatsapp.order_confirmation_sent",
        category=_WA,
        applies_to=_A.ORDER_CONFIRMATION,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Auftragsbestätigung versandt",
            _FR: "Envoi de la confirmation de commande",
            _IT: "Invio della conferma d'ordine",
            _EN: "Order confirmation sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nVielen Dank für Ihre Bestellung. Die Auftragsbestätigung "
            "{{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}} finden Sie über den "
            f"Link in dieser Nachricht.\n\n{_QUESTIONS_DE}\n\n{_SIGN_DE}",
            _FR: "{{customer.salutation}},\n\nNous vous remercions de votre commande. Vous trouverez la confirmation "
            "de commande {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}} via le "
            f"lien figurant dans ce message.\n\n{_QUESTIONS_FR}\n\n{_SIGN_FR}",
            _IT: "{{customer.salutation}},\n\nla ringraziamo per il Suo ordine. Trova la conferma d'ordine "
            "{{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}} tramite il link "
            f"contenuto in questo messaggio.\n\n{_QUESTIONS_IT}\n\n{_SIGN_IT}",
            _EN: "{{customer.salutation}},\n\nThank you for your order. You can find the order confirmation "
            "{{document.number}} of {{document.date}} for the {{vehicle.make_model}} through the link in this "
            f"message.\n\n{_QUESTIONS_EN}\n\n{_SIGN_EN}",
        },
    ),
    # --- the sales contract ---
    SeedText(
        code="email.contract_sent",
        category=_MAIL,
        applies_to=_A.CONTRACT,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Kaufvertrag versandt",
            _FR: "Envoi du contrat de vente",
            _IT: "Invio del contratto di compravendita",
            _EN: "Sales contract sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nIm Anhang finden Sie den Kaufvertrag {{document.number}} vom "
            "{{document.date}} für das Fahrzeug {{vehicle.make_model}}.\n\nVielen Dank für Ihr Vertrauen. "
            f"{_QUESTIONS_DE}\n\n{_SIGN_DE}",
            _FR: "{{customer.salutation}},\n\nVous trouverez en annexe le contrat de vente {{document.number}} du "
            "{{document.date}} pour le véhicule {{vehicle.make_model}}.\n\nNous vous remercions de votre confiance. "
            f"{_QUESTIONS_FR}\n\n{_SIGN_FR}",
            _IT: "{{customer.salutation}},\n\nin allegato trova il contratto di compravendita {{document.number}} del "
            "{{document.date}} per il veicolo {{vehicle.make_model}}.\n\nLa ringraziamo per la fiducia. "
            f"{_QUESTIONS_IT}\n\n{_SIGN_IT}",
            _EN: "{{customer.salutation}},\n\nPlease find attached the sales contract {{document.number}} of "
            "{{document.date}} for the {{vehicle.make_model}}.\n\nThank you for your trust. "
            f"{_QUESTIONS_EN}\n\n{_SIGN_EN}",
        },
    ),
    SeedText(
        code="sms.contract_sent",
        category=_SMS,
        applies_to=_A.CONTRACT,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Kaufvertrag versandt",
            _FR: "Envoi du contrat de vente",
            _IT: "Invio del contratto di compravendita",
            _EN: "Sales contract sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\nDen Kaufvertrag {{document.number}} für {{vehicle.make_model}} finden Sie "
            "über den Link in dieser Nachricht.\nFreundliche Grüsse\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\nvous trouverez le contrat de vente {{document.number}} pour le véhicule "
            "{{vehicle.make_model}} via le lien figurant dans ce message.\nMeilleures salutations\n"
            "{{dealership.name}}",
            _IT: "{{customer.salutation}},\ntrova il contratto di compravendita {{document.number}} per il veicolo "
            "{{vehicle.make_model}} tramite il link contenuto in questo messaggio.\nCordiali saluti\n"
            "{{dealership.name}}",
            _EN: "{{customer.salutation}},\nyou can find the sales contract {{document.number}} for the "
            "{{vehicle.make_model}} through the link in this message.\nKind regards\n{{dealership.name}}",
        },
    ),
    SeedText(
        code="whatsapp.contract_sent",
        category=_WA,
        applies_to=_A.CONTRACT,
        slot=_BODY,
        sort_order=10,
        titles={
            _DE: "Kaufvertrag versandt",
            _FR: "Envoi du contrat de vente",
            _IT: "Invio del contratto di compravendita",
            _EN: "Sales contract sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nDen Kaufvertrag {{document.number}} vom {{document.date}} für das "
            "Fahrzeug {{vehicle.make_model}} finden Sie über den Link in dieser Nachricht.\n\nVielen Dank für Ihr "
            f"Vertrauen. {_QUESTIONS_DE}\n\n{_SIGN_DE}",
            _FR: "{{customer.salutation}},\n\nVous trouverez le contrat de vente {{document.number}} du "
            "{{document.date}} pour le véhicule {{vehicle.make_model}} via le lien figurant dans ce message.\n\n"
            f"Nous vous remercions de votre confiance. {_QUESTIONS_FR}\n\n{_SIGN_FR}",
            _IT: "{{customer.salutation}},\n\ntrova il contratto di compravendita {{document.number}} del "
            "{{document.date}} per il veicolo {{vehicle.make_model}} tramite il link contenuto in questo "
            f"messaggio.\n\nLa ringraziamo per la fiducia. {_QUESTIONS_IT}\n\n{_SIGN_IT}",
            _EN: "{{customer.salutation}},\n\nYou can find the sales contract {{document.number}} of "
            "{{document.date}} for the {{vehicle.make_model}} through the link in this message.\n\nThank you for "
            f"your trust. {_QUESTIONS_EN}\n\n{_SIGN_EN}",
        },
    ),
)

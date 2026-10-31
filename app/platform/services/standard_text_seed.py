"""The seeded starter library (KAN-208; PRD "Standard texts" › The seeded
starter set, FR-A-08, D-A-26, A-28).

What a new Betrieb needs to issue a correct document on day one, in all four
languages: payment terms, late-payment notice, delivery and handover terms,
warranty wording, trade-in conditions, data-protection notice, offer
validity, and the e-mail bodies for offer sent / appointment reminder /
vehicle ready / invoice enclosed — plus the offer's introduction, which the
offer document prints.

**These are templates, not legal advice** (FR-A-08): Swiss-standard wording
(Swiss German spelling, «ss» never «ß»; OR / DSG references), labelled
«Vorlage — bitte prüfen» in the UI until A-7 (legal review before the first
customer) is answered. Person nouns follow the generic masculine (A-RULE-8 /
KAN-171).

One source for the rows: the KAN-208 migration inserts them and the test
fixture installs them through `install_platform_seeds`. After installation
the database rows are the seed library — `platform_admin` edits them per code
and language (FR-A-29, versioned); changing this module later does not touch
an installed seed.

The code's shape is `<applies_to>.<slot>.<name>` for a document text and
`<category>.<type>` for a message body.
"""

from dataclasses import dataclass

from app.core.i18n import SwissLanguage
from app.platform.models.standard_text import StandardTextAppliesTo, StandardTextSlot
from app.platform.models.standard_text_delegation import StandardTextCategory

_DE, _FR, _IT, _EN = SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN


@dataclass(frozen=True)
class SeedText:
    code: str
    category: StandardTextCategory
    applies_to: StandardTextAppliesTo
    slot: StandardTextSlot
    sort_order: int
    titles: dict[SwissLanguage, str]
    bodies: dict[SwissLanguage, str]
    is_default: bool = True


_DOC = StandardTextCategory.DOCUMENT
_MAIL = StandardTextCategory.EMAIL
_A = StandardTextAppliesTo
_S = StandardTextSlot

SEED_TEXTS: tuple[SeedText, ...] = (
    SeedText(
        code="offer.intro.standard",
        category=_DOC,
        applies_to=_A.OFFER,
        slot=_S.INTRO,
        sort_order=10,
        titles={
            _DE: "Einleitung der Offerte",
            _FR: "Introduction de l'offre",
            _IT: "Introduzione dell'offerta",
            _EN: "Offer introduction",
        },
        bodies={
            _DE: "Vielen Dank für Ihr Interesse. Gerne unterbreiten wir Ihnen die folgende Offerte.",
            _FR: "Nous vous remercions de votre intérêt et avons le plaisir de vous soumettre l'offre suivante.",
            _IT: "La ringraziamo per il Suo interesse e siamo lieti di sottoporLe la seguente offerta.",
            _EN: "Thank you for your interest. We are pleased to submit the following offer.",
        },
    ),
    SeedText(
        code="offer.footer.validity",
        category=_DOC,
        applies_to=_A.OFFER,
        slot=_S.FOOTER,
        sort_order=10,
        titles={
            _DE: "Gültigkeit der Offerte",
            _FR: "Validité de l'offre",
            _IT: "Validità dell'offerta",
            _EN: "Offer validity",
        },
        bodies={
            _DE: "Diese Offerte ist 30 Tage ab dem {{document.date}} gültig. "
            "Zwischenverkauf und Preisänderungen vorbehalten.",
            _FR: "Cette offre est valable 30 jours à compter du {{document.date}}. "
            "Sous réserve de vente intermédiaire et de modification des prix.",
            _IT: "La presente offerta è valida 30 giorni a partire dal {{document.date}}. "
            "Con riserva di vendita intermedia e di modifiche di prezzo.",
            _EN: "This offer is valid for 30 days from {{document.date}}. Subject to prior sale and price changes.",
        },
    ),
    SeedText(
        code="offer.terms.trade_in",
        category=_DOC,
        applies_to=_A.OFFER,
        slot=_S.TERMS,
        sort_order=10,
        titles={
            _DE: "Eintauschbedingungen",
            _FR: "Conditions de reprise",
            _IT: "Condizioni di permuta",
            _EN: "Trade-in conditions",
        },
        bodies={
            _DE: "Der Eintauschpreis gilt unter Vorbehalt einer Besichtigung des Fahrzeugs bei der Übergabe. "
            "Weicht dessen Zustand (Kilometerstand, Schäden, Ausstattung) von den gemachten Angaben ab, behalten "
            "wir uns eine Anpassung vor. Das Fahrzeug ist frei von Rechten Dritter zu übergeben.",
            _FR: "Le prix de reprise est valable sous réserve d'une inspection du véhicule lors de sa remise. "
            "Si son état (kilométrage, dommages, équipement) diffère des indications fournies, nous nous "
            "réservons le droit d'adapter le prix. Le véhicule doit être remis libre de tout droit de tiers.",
            _IT: "Il prezzo di permuta è valido con riserva di un controllo del veicolo al momento della consegna. "
            "Se il suo stato (chilometraggio, danni, equipaggiamento) differisce dalle indicazioni fornite, ci "
            "riserviamo di adeguare il prezzo. Il veicolo deve essere consegnato libero da diritti di terzi.",
            _EN: "The trade-in price is subject to an inspection of the vehicle at handover. If its condition "
            "(mileage, damage, equipment) differs from the information provided, we reserve the right to adjust "
            "the price. The vehicle must be handed over free of third-party rights.",
        },
    ),
    SeedText(
        code="contract.terms.delivery",
        category=_DOC,
        applies_to=_A.CONTRACT,
        slot=_S.TERMS,
        sort_order=10,
        titles={
            _DE: "Liefer- und Übergabebedingungen",
            _FR: "Conditions de livraison et de remise",
            _IT: "Condizioni di fornitura e di consegna",
            _EN: "Delivery and handover terms",
        },
        bodies={
            _DE: "Die Übergabe des Fahrzeugs erfolgt nach vollständiger Bezahlung des Kaufpreises. Nutzen und "
            "Gefahr gehen mit der Übergabe auf den Käufer über. Angaben zu Lieferterminen sind unverbindlich, "
            "sofern sie nicht ausdrücklich schriftlich als verbindlich vereinbart wurden.",
            _FR: "La remise du véhicule a lieu après paiement intégral du prix de vente. Les profits et les "
            "risques passent à l'acheteur lors de la remise. Les délais de livraison indiqués sont sans "
            "engagement, sauf s'ils ont été expressément convenus par écrit comme contraignants.",
            _IT: "La consegna del veicolo avviene dopo il pagamento integrale del prezzo di vendita. Utili e "
            "rischi passano all'acquirente al momento della consegna. I termini di consegna indicati non sono "
            "vincolanti, salvo che siano stati espressamente convenuti per iscritto come vincolanti.",
            _EN: "The vehicle is handed over once the purchase price has been paid in full. Benefit and risk pass "
            "to the buyer on handover. Delivery dates are non-binding unless they have been expressly agreed as "
            "binding in writing.",
        },
    ),
    SeedText(
        code="contract.closing.warranty",
        category=_DOC,
        applies_to=_A.CONTRACT,
        slot=_S.CLOSING,
        sort_order=10,
        titles={
            _DE: "Garantie und Gewährleistung",
            _FR: "Garantie",
            _IT: "Garanzia",
            _EN: "Warranty",
        },
        bodies={
            _DE: "Für das Fahrzeug gilt die Garantie gemäss den Bedingungen des Herstellers oder die vereinbarte "
            "Occasionsgarantie. Eine darüber hinausgehende Gewährleistung wird, soweit gesetzlich zulässig, "
            "wegbedungen (Art. 199 OR).",
            _FR: "Le véhicule bénéficie de la garantie selon les conditions du constructeur ou de la garantie "
            "occasion convenue. Toute garantie allant au-delà est exclue dans la mesure permise par la loi "
            "(art. 199 CO).",
            _IT: "Per il veicolo vale la garanzia secondo le condizioni del costruttore o la garanzia occasione "
            "convenuta. Ogni ulteriore garanzia è esclusa nella misura consentita dalla legge (art. 199 CO).",
            _EN: "The vehicle is covered by the manufacturer's warranty or by the agreed used-car warranty. Any "
            "further warranty is excluded to the extent permitted by law (Art. 199 CO).",
        },
    ),
    SeedText(
        code="contract.footer.data_protection",
        category=_DOC,
        applies_to=_A.CONTRACT,
        slot=_S.FOOTER,
        sort_order=10,
        titles={
            _DE: "Datenschutzhinweis",
            _FR: "Information sur la protection des données",
            _IT: "Informativa sulla protezione dei dati",
            _EN: "Data-protection notice",
        },
        bodies={
            _DE: "{{dealership.name}} bearbeitet Ihre Personendaten gemäss dem Bundesgesetz über den Datenschutz "
            "(DSG) zur Abwicklung dieses Vertrags. Einzelheiten finden Sie in unserer Datenschutzerklärung.",
            _FR: "{{dealership.name}} traite vos données personnelles conformément à la loi fédérale sur la "
            "protection des données (LPD) aux fins de l'exécution du présent contrat. Vous trouverez les détails "
            "dans notre déclaration de protection des données.",
            _IT: "{{dealership.name}} tratta i Suoi dati personali conformemente alla legge federale sulla "
            "protezione dei dati (LPD) ai fini dell'esecuzione del presente contratto. I dettagli figurano nella "
            "nostra dichiarazione sulla protezione dei dati.",
            _EN: "{{dealership.name}} processes your personal data in accordance with the Swiss Federal Act on "
            "Data Protection (FADP) for the purpose of performing this contract. Details are set out in our "
            "privacy notice.",
        },
    ),
    SeedText(
        code="invoice.footer.payment_terms",
        category=_DOC,
        applies_to=_A.INVOICE,
        slot=_S.FOOTER,
        sort_order=10,
        titles={
            _DE: "Zahlungsbedingungen",
            _FR: "Conditions de paiement",
            _IT: "Condizioni di pagamento",
            _EN: "Payment terms",
        },
        bodies={
            _DE: "Zahlbar bis {{payment.due_date}} ohne Abzug.",
            _FR: "Payable sans déduction jusqu'au {{payment.due_date}}.",
            _IT: "Pagabile senza deduzioni entro il {{payment.due_date}}.",
            _EN: "Payable without deduction by {{payment.due_date}}.",
        },
    ),
    SeedText(
        code="invoice.terms.late_payment",
        category=_DOC,
        applies_to=_A.INVOICE,
        slot=_S.TERMS,
        sort_order=10,
        titles={
            _DE: "Zahlungsverzug",
            _FR: "Retard de paiement",
            _IT: "Ritardo nel pagamento",
            _EN: "Late payment",
        },
        bodies={
            _DE: "Mit Ablauf der Zahlungsfrist am {{payment.due_date}} geraten Sie ohne weitere Mahnung in Verzug; "
            "geschuldet ist ein Verzugszins von 5 % pro Jahr (Art. 102 und 104 OR).",
            _FR: "À l'expiration du délai de paiement, le {{payment.due_date}}, vous êtes en demeure sans autre "
            "avertissement ; un intérêt moratoire de 5 % l'an est dû (art. 102 et 104 CO).",
            _IT: "Alla scadenza del termine di pagamento, il {{payment.due_date}}, Lei è in mora senza ulteriore "
            "diffida; è dovuto un interesse di mora del 5 % annuo (art. 102 e 104 CO).",
            _EN: "Once the payment deadline of {{payment.due_date}} has passed, you are in default without further "
            "reminder; default interest of 5% per annum is owed (Art. 102 and 104 CO).",
        },
    ),
    SeedText(
        code="email.offer_sent",
        category=_MAIL,
        applies_to=_A.OFFER,
        slot=_S.BODY,
        sort_order=10,
        titles={
            _DE: "Offerte versandt",
            _FR: "Envoi de l'offre",
            _IT: "Invio dell'offerta",
            _EN: "Offer sent",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nVielen Dank für Ihr Interesse. Im Anhang finden Sie unsere Offerte "
            "{{document.number}} vom {{document.date}} für das Fahrzeug {{vehicle.make_model}}.\n\nFür Fragen "
            "stehe ich Ihnen gerne zur Verfügung.\n\nFreundliche Grüsse\n{{user.full_name}}\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\n\nNous vous remercions de votre intérêt. Vous trouverez en annexe notre "
            "offre {{document.number}} du {{document.date}} pour le véhicule {{vehicle.make_model}}.\n\nJe reste "
            "volontiers à votre disposition pour toute question.\n\nMeilleures salutations\n{{user.full_name}}\n"
            "{{dealership.name}}",
            _IT: "{{customer.salutation}},\n\nla ringraziamo per il Suo interesse. In allegato trova la nostra "
            "offerta {{document.number}} del {{document.date}} per il veicolo {{vehicle.make_model}}.\n\nResto "
            "volentieri a Sua disposizione per qualsiasi domanda.\n\nCordiali saluti\n{{user.full_name}}\n"
            "{{dealership.name}}",
            _EN: "{{customer.salutation}},\n\nThank you for your interest. Please find attached our offer "
            "{{document.number}} of {{document.date}} for the {{vehicle.make_model}}.\n\nI will be glad to answer "
            "any questions.\n\nKind regards\n{{user.full_name}}\n{{dealership.name}}",
        },
    ),
    SeedText(
        code="email.appointment_reminder",
        category=_MAIL,
        applies_to=_A.APPOINTMENT_REMINDER,
        slot=_S.BODY,
        sort_order=10,
        titles={
            _DE: "Terminerinnerung",
            _FR: "Rappel de rendez-vous",
            _IT: "Promemoria appuntamento",
            _EN: "Appointment reminder",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nGerne erinnern wir Sie an Ihren bevorstehenden Termin für das Fahrzeug "
            "{{vehicle.make_model}}. Die Einzelheiten finden Sie im Anhang. Können Sie den Termin nicht "
            "wahrnehmen, erreichen Sie uns unter {{location.phone}}.\n\nFreundliche Grüsse\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\n\nNous vous rappelons votre prochain rendez-vous pour le véhicule "
            "{{vehicle.make_model}}. Vous en trouverez les détails en annexe. Si vous ne pouvez pas l'honorer, "
            "vous pouvez nous joindre au {{location.phone}}.\n\nMeilleures salutations\n{{dealership.name}}",
            _IT: "{{customer.salutation}},\n\nle ricordiamo il Suo prossimo appuntamento per il veicolo "
            "{{vehicle.make_model}}. Trova i dettagli in allegato. Se non potesse presentarsi, ci può raggiungere "
            "al numero {{location.phone}}.\n\nCordiali saluti\n{{dealership.name}}",
            _EN: "{{customer.salutation}},\n\nThis is a reminder of your upcoming appointment for the "
            "{{vehicle.make_model}}. Please find the details attached. If you cannot attend, you can reach us on "
            "{{location.phone}}.\n\nKind regards\n{{dealership.name}}",
        },
    ),
    SeedText(
        code="email.vehicle_ready",
        category=_MAIL,
        applies_to=_A.VEHICLE_READY,
        slot=_S.BODY,
        sort_order=10,
        titles={
            _DE: "Fahrzeug abholbereit",
            _FR: "Véhicule prêt",
            _IT: "Veicolo pronto",
            _EN: "Vehicle ready",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nIhr Fahrzeug {{vehicle.make_model}} ist bereit und kann abgeholt "
            "werden. Für einen Abholtermin erreichen Sie uns unter {{location.phone}}.\n\nFreundliche Grüsse\n"
            "{{user.full_name}}\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\n\nVotre véhicule {{vehicle.make_model}} est prêt et peut être retiré. "
            "Pour convenir d'un rendez-vous, vous pouvez nous joindre au {{location.phone}}.\n\nMeilleures "
            "salutations\n{{user.full_name}}\n{{dealership.name}}",
            _IT: "{{customer.salutation}},\n\nil Suo veicolo {{vehicle.make_model}} è pronto e può essere ritirato. "
            "Per fissare un appuntamento ci può raggiungere al numero {{location.phone}}.\n\nCordiali saluti\n"
            "{{user.full_name}}\n{{dealership.name}}",
            _EN: "{{customer.salutation}},\n\nYour {{vehicle.make_model}} is ready for collection. To arrange a "
            "time, you can reach us on {{location.phone}}.\n\nKind regards\n{{user.full_name}}\n"
            "{{dealership.name}}",
        },
    ),
    SeedText(
        code="email.invoice_enclosed",
        category=_MAIL,
        applies_to=_A.INVOICE,
        slot=_S.BODY,
        sort_order=10,
        titles={
            _DE: "Rechnung im Anhang",
            _FR: "Facture en annexe",
            _IT: "Fattura in allegato",
            _EN: "Invoice enclosed",
        },
        bodies={
            _DE: "{{customer.salutation}}\n\nIm Anhang erhalten Sie unsere Rechnung {{document.number}} vom "
            "{{document.date}}. Wir bitten Sie, den Betrag bis {{payment.due_date}} zu begleichen.\n\nBesten Dank "
            "für Ihr Vertrauen.\n\nFreundliche Grüsse\n{{dealership.name}}",
            _FR: "{{customer.salutation}},\n\nVous trouverez en annexe notre facture {{document.number}} du "
            "{{document.date}}. Nous vous prions de bien vouloir en régler le montant d'ici au "
            "{{payment.due_date}}.\n\nNous vous remercions de votre confiance.\n\nMeilleures salutations\n"
            "{{dealership.name}}",
            _IT: "{{customer.salutation}},\n\nin allegato trova la nostra fattura {{document.number}} del "
            "{{document.date}}. La preghiamo di saldare l'importo entro il {{payment.due_date}}.\n\nLa "
            "ringraziamo per la fiducia.\n\nCordiali saluti\n{{dealership.name}}",
            _EN: "{{customer.salutation}},\n\nPlease find attached our invoice {{document.number}} of "
            "{{document.date}}. We kindly ask you to settle the amount by {{payment.due_date}}.\n\nThank you for "
            "your trust.\n\nKind regards\n{{dealership.name}}",
        },
    ),
)


SEED_CODES = tuple(seed.code for seed in SEED_TEXTS)

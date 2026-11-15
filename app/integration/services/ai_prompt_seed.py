"""The AI prompts Nexotec ships (KAN-227, D-A-33): `vehicle_listing` — the
description of a car for its marketplace listing and the Betrieb's website —
in DE/FR/IT/EN (Swiss German spelling, «ss» never «ß»).

What the application reads as «the shipped wording»: the Translations panel
diffs a seed against it and a revert restores it. The rows themselves are
installed by the integration migration `e7a2c91d4b60` as **literals** (ADR-133)
— `tests/test_ai_prompts.py` keeps the two equal; a later edition of these
prompts is a new data migration and a change here together, never this module
alone.

The instructions keep the suggestion honest: only the facts given, nothing
invented about history, accidents, warranty or equipment, no price promise
beyond the price given, no contact details, plain text. The user always
edits the result; nothing is published from it (FR-A-38).
"""

from dataclasses import dataclass

from app.core.i18n import SwissLanguage
from app.integration.services.ai_prompt_registry import VEHICLE_LISTING

_DE, _FR, _IT, _EN = SwissLanguage.DE, SwissLanguage.FR, SwissLanguage.IT, SwissLanguage.EN


@dataclass(frozen=True)
class SeedPrompt:
    code: str
    titles: dict[SwissLanguage, str]
    system_prompts: dict[SwissLanguage, str]
    user_prompts: dict[SwissLanguage, str]


_FACTS_DE = (
    "Fahrzeug: {{ vehicle.label }}\n"
    "Zustand: {{ vehicle.condition }}\n"
    "Karosserie: {{ vehicle.body_style }}\n"
    "Aussenfarbe: {{ vehicle.exterior_colour }}\n"
    "Kilometerstand: {{ vehicle.odometer_km }}\n"
    "1. Inverkehrsetzung: {{ vehicle.first_registration }}\n"
    "Preis: {{ vehicle.price }}\n"
    "Ausstattung: {{ vehicle.equipment }}"
)
_FACTS_FR = (
    "Véhicule : {{ vehicle.label }}\n"
    "État : {{ vehicle.condition }}\n"
    "Carrosserie : {{ vehicle.body_style }}\n"
    "Couleur extérieure : {{ vehicle.exterior_colour }}\n"
    "Kilométrage : {{ vehicle.odometer_km }}\n"
    "1re mise en circulation : {{ vehicle.first_registration }}\n"
    "Prix : {{ vehicle.price }}\n"
    "Équipement : {{ vehicle.equipment }}"
)
_FACTS_IT = (
    "Veicolo: {{ vehicle.label }}\n"
    "Stato: {{ vehicle.condition }}\n"
    "Carrozzeria: {{ vehicle.body_style }}\n"
    "Colore esterno: {{ vehicle.exterior_colour }}\n"
    "Chilometraggio: {{ vehicle.odometer_km }}\n"
    "1a immatricolazione: {{ vehicle.first_registration }}\n"
    "Prezzo: {{ vehicle.price }}\n"
    "Equipaggiamento: {{ vehicle.equipment }}"
)
_FACTS_EN = (
    "Vehicle: {{ vehicle.label }}\n"
    "Condition: {{ vehicle.condition }}\n"
    "Body style: {{ vehicle.body_style }}\n"
    "Exterior colour: {{ vehicle.exterior_colour }}\n"
    "Mileage: {{ vehicle.odometer_km }}\n"
    "First registration: {{ vehicle.first_registration }}\n"
    "Price: {{ vehicle.price }}\n"
    "Equipment: {{ vehicle.equipment }}"
)

SEED_PROMPTS: tuple[SeedPrompt, ...] = (
    SeedPrompt(
        code=VEHICLE_LISTING,
        titles={
            _DE: "Inseratetext für ein Fahrzeug",
            _FR: "Texte d'annonce pour un véhicule",
            _IT: "Testo dell'annuncio per un veicolo",
            _EN: "Listing text for a vehicle",
        },
        system_prompts={
            _DE: (
                "Du schreibst Inseratetexte für ein Autohaus in der Schweiz. Schreibe auf Deutsch in "
                "Schweizer Rechtschreibung (immer «ss», nie «ß»), sachlich und freundlich, 80 bis 150 Wörter, "
                "als Fliesstext ohne Aufzählungszeichen, Überschriften oder Markdown. Verwende ausschliesslich "
                "die genannten Angaben. Erfinde nichts: keine Ausstattung, keine Vorgeschichte, keine Unfallfreiheit, "
                "keine Garantie, keinen Service und keine Kontaktdaten. Eine Angabe «keine Angabe» lässt du weg."
            ),
            _FR: (
                "Tu rédiges des textes d'annonce pour un garage en Suisse. Écris en français, sur un ton factuel "
                "et aimable, entre 80 et 150 mots, en texte continu sans puces, titres ni Markdown. Utilise "
                "uniquement les informations fournies. N'invente rien : aucun équipement, aucun historique, "
                "aucune absence d'accident, aucune garantie, aucun service et aucune coordonnée. Omets toute "
                "information « non indiqué »."
            ),
            _IT: (
                "Scrivi testi di annunci per un garage in Svizzera. Scrivi in italiano, con tono oggettivo e "
                "cordiale, da 80 a 150 parole, in testo continuo senza elenchi puntati, titoli o Markdown. Usa "
                "esclusivamente le informazioni fornite. Non inventare nulla: nessun equipaggiamento, nessuna "
                "storia, nessuna assenza di incidenti, nessuna garanzia, nessun servizio e nessun recapito. "
                "Ometti ogni informazione «non indicato»."
            ),
            _EN: (
                "You write listing texts for a car dealership in Switzerland. Write in English, factual and "
                "friendly, 80 to 150 words, as running text without bullet points, headings or Markdown. Use "
                "only the facts given. Invent nothing: no equipment, no history, no accident-free claim, no "
                "warranty, no service and no contact details. Leave out any fact marked «not stated»."
            ),
        },
        user_prompts={
            _DE: "Schreibe den Inseratetext für dieses Fahrzeug.\n\n" + _FACTS_DE,
            _FR: "Rédige le texte d'annonce de ce véhicule.\n\n" + _FACTS_FR,
            _IT: "Scrivi il testo dell'annuncio per questo veicolo.\n\n" + _FACTS_IT,
            _EN: "Write the listing text for this vehicle.\n\n" + _FACTS_EN,
        },
    ),
)


def shipped_prompt(code: str, language: SwissLanguage) -> tuple[str, str, str] | None:
    """(title, system prompt, user prompt) as Nexotec ships it, or None."""

    for seed in SEED_PROMPTS:
        if seed.code == code:
            return seed.titles[language], seed.system_prompts[language], seed.user_prompts[language]
    return None

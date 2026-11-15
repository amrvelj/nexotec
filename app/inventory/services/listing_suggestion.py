"""«Text vorschlagen» (KAN-227, D-A-33, FR-A-38) — a listing description
suggested by the Betrieb's own AI provider, from the stock item's facts.

The smallest consumer of `integration.public.generate`: Stock gathers the
car's facts in the prompt's language (`vehicle_listing`'s placeholders, the
registry in `app/integration/services/ai_prompt_registry.py`) and hands back
the provider's text. **It writes nothing**: the suggestion is returned to the
screen, where the user takes it into the description field, edits it and
saves it like any text — never published by this call (FR-A-38: "output
always editable, never auto-published").

What goes into the prompt is the Betrieb's own data about its own car,
inside its own request (licence §3: provider data only inside the tenant's
request and response). The equipment is the vehicle's provider equipment
text in the prompt's language when vehicle-mdm holds one, else its extras;
condition flags (`eigenschaften`, an accident history among them) are not
equipment and are never sent.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.i18n import SwissLanguage, format_currency_chf, format_date_ch, format_number_ch
from app.integration.public import AI_PROMPT_VEHICLE_LISTING, GeneratedText, generate
from app.inventory.models.stock_item import StockItem, StockItemCondition
from app.platform.public import get_dealership_default_correspondence_language
from app.vehicle.public import get_vehicle_equipment

_CONDITION: dict[StockItemCondition, dict[SwissLanguage, str]] = {
    StockItemCondition.NEW: {SwissLanguage.DE: "Neuwagen", SwissLanguage.FR: "Véhicule neuf", SwissLanguage.IT: "Veicolo nuovo", SwissLanguage.EN: "New car"},
    StockItemCondition.USED: {SwissLanguage.DE: "Occasion", SwissLanguage.FR: "Occasion", SwissLanguage.IT: "Occasione", SwissLanguage.EN: "Used car"},
    StockItemCondition.DEMO: {SwissLanguage.DE: "Vorführwagen", SwissLanguage.FR: "Véhicule de démonstration", SwissLanguage.IT: "Veicolo dimostrativo", SwissLanguage.EN: "Demonstration car"},
    StockItemCondition.TAGESZ: {SwissLanguage.DE: "Tageszulassung", SwissLanguage.FR: "Immatriculation journalière", SwissLanguage.IT: "Immatricolazione giornaliera", SwissLanguage.EN: "Pre-registered car"},
}


def _price(item: StockItem) -> Decimal | None:
    # `is None`, never `or`: a price of 0.00 is falsy and still a price.
    if item.effective_price is not None:
        return item.effective_price
    return item.list_price


def _equipment(db: Session, item: StockItem, language: SwissLanguage) -> str | None:
    if item.vehicle_id is None:
        return None
    equipment = get_vehicle_equipment(db, item.vehicle_id)
    provider_text = (equipment.get("providerAusstattung") or {}).get(language.value)
    if isinstance(provider_text, str) and provider_text.strip():
        return provider_text.strip()
    extras = [str(extra) for extra in equipment.get("extras") or [] if str(extra).strip()]
    return ", ".join(extras) or None


def listing_variables(db: Session, item: StockItem, language: SwissLanguage) -> dict[str, str | None]:
    price = _price(item)
    return {
        "vehicle.label": item.vehicle_label,
        "vehicle.condition": _CONDITION[item.condition][language],
        "vehicle.body_style": item.body_style,
        "vehicle.exterior_colour": item.exterior_colour,
        "vehicle.odometer_km": f"{format_number_ch(item.odometer_km)} km" if item.odometer_km is not None else None,
        "vehicle.first_registration": format_date_ch(item.first_registration_date) if item.first_registration_date else None,
        "vehicle.price": format_currency_chf(price) if price is not None else None,
        "vehicle.equipment": _equipment(db, item, language),
    }


def suggest_listing_text(
    db: Session, *, item: StockItem, language: SwissLanguage | None, actor_id: uuid.UUID | None
) -> GeneratedText:
    """The prompt resolves its own language (asked → the Betrieb's
    correspondence language → DE); the facts are formatted in the language
    asked for, or the Betrieb's when none was — the same one the prompt
    lands on unless the Betrieb lacks a prompt in it, where DE wording with
    Swiss-formatted numbers is still right."""

    facts_language = language or get_dealership_default_correspondence_language(db, item.tenant_id)
    return generate(
        db,
        dealership_id=item.tenant_id,
        code=AI_PROMPT_VEHICLE_LISTING,
        variables=listing_variables(db, item, facts_language),
        language=language,
        actor_id=actor_id,
    )

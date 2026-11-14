"""What any listing of a stock item needs before it exists (WP-7 PR-8,
ADR-062), named before send — shared by the marketplace channels
(`publishing.py`) and the Betrieb's website (`website_listing.py`, KAN-225).
Each refusal names the marketplace's own field (AS24i's, which drives the
canonical mapping)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.models.stock_item import LifecycleStatus, StockItem, StockItemCondition
from app.inventory.models.stock_item_publishing import StockItemMedia
from app.inventory.schemas.publishing import BlockingCondition
from app.vehicle.public import has_current_energy_rating

_NEW_CAR_ENERGY_LABEL_ODOMETER_THRESHOLD_KM = 2000


def compute_blocking_conditions(db: Session, item: StockItem) -> list[BlockingCondition]:
    conditions: list[BlockingCondition] = []

    if item.lifecycle_status == LifecycleStatus.PIPELINE:
        conditions.append(
            BlockingCondition(field="Lagerstatus", message="Eine Werksbestellung steht noch nicht am Lager.")
        )

    media_count = db.scalar(select(StockItemMedia.id).where(StockItemMedia.stock_item_id == item.id).limit(1))
    if media_count is None:
        conditions.append(BlockingCondition(field="Bilder", message="Keine eigenen Fotografien vorhanden."))

    if item.effective_price is None:
        conditions.append(BlockingCondition(field="Preis", message="Kein Effektivpreis hinterlegt."))

    if not item.exterior_colour:
        conditions.append(BlockingCondition(field="Aussenfarbe", message="Keine Farbe hinterlegt."))

    if not item.body_style:
        conditions.append(BlockingCondition(field="Aufbau", message="Keine Karosserieform hinterlegt."))

    if item.odometer_km is None:
        conditions.append(BlockingCondition(field="Kilometer", message="Kein Kilometerstand hinterlegt."))

    # AS24i's own carve-out (Schnittstellenbeschrieb v34 p8): a NEW car
    # ships InvSetzJahr from its model year instead, since it may not have
    # been registered yet — so this only blocks a used/demo/Tageszulassung
    # item, which the spec's own wording requires a real date for.
    if item.condition != StockItemCondition.NEW and item.first_registration_date is None:
        conditions.append(BlockingCondition(field="InvSetzJahr", message="Keine Erstzulassung hinterlegt."))

    if (
        item.condition == StockItemCondition.NEW
        and item.odometer_km is not None
        and item.odometer_km < _NEW_CAR_ENERGY_LABEL_ODOMETER_THRESHOLD_KM
        and item.vehicle_id is not None
        and not has_current_energy_rating(db, item.vehicle_id)
    ):
        conditions.append(
            BlockingCondition(field="Energieetikette", message="Energieetikette fehlt für Neuwagen unter 2000 km.")
        )

    return conditions


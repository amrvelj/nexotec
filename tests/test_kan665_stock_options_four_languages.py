"""KAN-665: a stock car with a configuration carries the identAPI's four
option texts into its offer's frozen factory-option lines, as a configured
car does since KAN-595 (Path B). Stock's option set reads them from the
configuration through `vehicle.public` — never from the client — matched by
the option's code; an option the configuration does not name (or names
without provider texts) keeps its one text for every language."""

import uuid
from decimal import Decimal

from app.inventory.models.stock_item import StockItemCondition
from app.inventory.public import get_stock_item_pricing
from app.inventory.schemas.pricing import OptionInput
from app.inventory.schemas.stock_item import StockItemCreate
from app.inventory.services.pricing import set_options
from app.inventory.services.stock_item import create_stock_item
from app.sales.models.line_item import SalesLineItem
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.offer import create_offer, update_offer
from app.vehicle.schemas.configuration import ConfigurationOptionInput
from tests.test_configurator_hosts import _dealership, _variant
from tests.test_option_texts_four_languages import _catalogue_option, _configured


def _stock_car_with_configuration(db_session):
    dealership = _dealership(db_session)
    variant = _variant(db_session)
    pump = _catalogue_option(
        db_session, tenant_id=dealership.id, variant=variant, code="HP1",
        description_de="Wärmepumpe", description_fr="Pompe à chaleur", description_it="Pompa di calore",
        description_en="Heat pump",
    )
    config = _configured(
        db_session, dealership.id, variant,
        [ConfigurationOptionInput(variant_option_id=pump.id, description="Wärmepumpe", option_code="HP1", price=Decimal("1250.00"))],
    )
    item = create_stock_item(
        db_session, tenant_id=dealership.id, actor_id=None,
        data=StockItemCreate(vehicle_label="Skoda Enyaq", condition=StockItemCondition.NEW, configuration_id=config.id),
    )
    set_options(
        db_session, item=item, base_price=Decimal("48000.00"), actor_id=None,
        options=[
            OptionInput(code="HP1", label="Wärmepumpe", price=Decimal("1250.00")),
            OptionInput(code="AHK", label="Anhängerkupplung", price=Decimal("900.00")),
        ],
    )
    return dealership, item


def test_stocks_option_set_carries_the_configurations_four_texts(db_session):
    dealership, item = _stock_car_with_configuration(db_session)

    pricing = get_stock_item_pricing(db_session, tenant_id=dealership.id, stock_item_id=item.id)

    labels = {o["code"]: o["labels"] for o in pricing["options"]}
    assert labels == {
        "HP1": {"de": "Wärmepumpe", "fr": "Pompe à chaleur", "it": "Pompa di calore", "en": "Heat pump"},
        "AHK": {},
    }


def test_a_stock_cars_offer_freezes_its_option_lines_in_four_languages(db_session):
    dealership, item = _stock_car_with_configuration(db_session)
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())

    update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), actor_id=None,
        data=OfferUpdate(vehicle_source="stock", stock_item_id=item.id, vehicle_label=item.vehicle_label),
    )

    pump = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, code="HP1").one()
    assert (pump.label, pump.label_de, pump.label_fr, pump.label_it, pump.label_en) == (
        "Wärmepumpe", "Wärmepumpe", "Pompe à chaleur", "Pompa di calore", "Heat pump",
    )
    hitch = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, code="AHK").one()
    assert (hitch.label, hitch.label_fr) == ("Anhängerkupplung", None)


def test_a_stock_car_without_a_configuration_keeps_one_text(db_session):
    dealership = _dealership(db_session)
    item = create_stock_item(
        db_session, tenant_id=dealership.id, actor_id=None,
        data=StockItemCreate(vehicle_label="Skoda Enyaq", condition=StockItemCondition.NEW),
    )
    set_options(
        db_session, item=item, base_price=Decimal("48000.00"), actor_id=None,
        options=[OptionInput(code="HP1", label="Wärmepumpe", price=Decimal("1250.00"))],
    )

    pricing = get_stock_item_pricing(db_session, tenant_id=dealership.id, stock_item_id=item.id)

    assert [o["labels"] for o in pricing["options"]] == [{}]

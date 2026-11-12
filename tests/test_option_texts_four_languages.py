"""KAN-595: catalogue options keep the identAPI's four texts
(`descriptionDe/Fr/It/En`), a configuration copies them from the Betrieb's
own catalogue row (never from the client), the configuration's price line
carries them into the offer's frozen factory-option lines, and the printed
offer names each option in the document's language — `label` (the
delivered default) where the provider sent none, never a loud marker."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.core.i18n import SwissLanguage
from app.platform.schemas.document_content import LineItemsBlock
from app.sales.models.line_item import SalesLineItem
from app.sales.schemas.offer import OfferUpdate
from app.sales.services.document import _included_option_lines, build_offer_content
from app.sales.services.offer import create_offer, update_offer
from app.vehicle.models.catalogue import VariantOption
from app.vehicle.models.configuration import ConfigurationMatchMethod, ConfigurationMode, ConfigurationSource
from app.vehicle.schemas.configuration import ConfigurationCreate, ConfigurationOptionInput
from app.vehicle.services import catalogue_entitlements
from app.vehicle.services import configuration as configuration_service
from tests import identapi_fixtures as fix
from tests.identapi_support import FakeAutoi
from tests.postgres_only import NEEDS_ITS_OWN_CONNECTION
from tests.test_catalogue_details_on_open import _connected, _fresh_adapter_state, _open
from tests.test_configurator_hosts import _dealership, _variant

_fresh = _fresh_adapter_state


@NEEDS_ITS_OWN_CONNECTION
def test_the_loader_stores_the_four_texts_and_the_specification_reads_them(db_session, monkeypatch):
    s, variant = _connected(db_session, monkeypatch, FakeAutoi(api_bodies=fix.detail_bodies()))

    _open(db_session, s, variant)

    option = db_session.query(VariantOption).filter_by(model_variant_id=variant.id, option_code="1001").one()
    assert (option.description, option.description_de, option.description_fr, option.description_it, option.description_en) == (
        "Option 1001", "Option 1001", "Option 1001 (fr)", "Option 1001 (it)", "Option 1001 (en)",
    )
    spec = catalogue_entitlements.get_catalogue_specification(
        db_session, tenant_id=s.connection.tenant_id, model_variant_id=variant.id, load_on_open=False
    )
    read = next(o for o in spec.options if o.option_code == "1001")
    assert read.descriptions.model_dump() == {
        "de": "Option 1001", "fr": "Option 1001 (fr)", "it": "Option 1001 (it)", "en": "Option 1001 (en)",
    }


def _catalogue_option(db_session, *, tenant_id, variant, code, **texts) -> VariantOption:
    row = VariantOption(
        tenant_id=tenant_id, model_variant_id=variant.id, option_code=code, description=texts.get("description_de") or code,
        price=Decimal("1250.00"), **texts,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _configured(db_session, tenant_id, variant, options):
    config = configuration_service.create_configuration(
        db_session, tenant_id=tenant_id, actor_id=uuid.uuid4(),
        data=ConfigurationCreate(
            source=ConfigurationSource.PROVIDER, mode=ConfigurationMode.BUILD,
            match_method=ConfigurationMatchMethod.CATALOGUE_BROWSE, catalogue_variant_id=variant.id,
        ),
    )
    db_session.commit()
    return configuration_service.replace_options(db_session, configuration=config, actor_id=uuid.uuid4(), options=options)


def test_a_configuration_copies_the_texts_from_its_own_betriebs_catalogue_row_only(db_session):
    dealership = _dealership(db_session)
    variant = _variant(db_session)
    pump = _catalogue_option(
        db_session, tenant_id=dealership.id, variant=variant, code="HP1",
        description_de="Wärmepumpe", description_fr="Pompe à chaleur", description_it="Pompa di calore",
        description_en="Heat pump",
    )
    elsewhere = _catalogue_option(
        db_session, tenant_id=uuid.uuid4(), variant=variant, code="PD2", description_fr="Toit d'un autre Betrieb",
    )

    config = _configured(
        db_session, dealership.id, variant,
        [
            ConfigurationOptionInput(variant_option_id=pump.id, description="Wärmepumpe", option_code="HP1", price=Decimal("1250.00")),
            # Another Betrieb's catalogue row: its licensed text is never copied (ADR-013).
            ConfigurationOptionInput(variant_option_id=elsewhere.id, description="Panoramadach", option_code="PD2", price=Decimal("1490.00")),
            ConfigurationOptionInput(description="Anhängerkupplung", option_code="AHK", price=Decimal("900.00")),
        ],
    )

    texts = {o.option_code: (o.description_de, o.description_fr, o.description_it, o.description_en) for o in config.options}
    assert texts == {
        "HP1": ("Wärmepumpe", "Pompe à chaleur", "Pompa di calore", "Heat pump"),
        "PD2": (None, None, None, None),
        "AHK": (None, None, None, None),
    }
    copy = configuration_service.copy_configuration(db_session, source=config, actor_id=None)
    assert {o.option_code: o.description_fr for o in copy.options} == {"HP1": "Pompe à chaleur", "PD2": None, "AHK": None}


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (SwissLanguage.FR, ["Pompe à chaleur", "Anhängerkupplung"]),
        (SwissLanguage.IT, ["Pompa di calore", "Anhängerkupplung"]),
        (SwissLanguage.EN, ["Heat pump", "Anhängerkupplung"]),
        (SwissLanguage.DE, ["Wärmepumpe", "Anhängerkupplung"]),
    ],
)
def test_the_offer_prints_each_option_in_the_documents_language(db_session, language, expected):
    dealership = _dealership(db_session)
    variant = _variant(db_session)
    pump = _catalogue_option(
        db_session, tenant_id=dealership.id, variant=variant, code="HP1",
        description_de="Wärmepumpe", description_fr="Pompe à chaleur", description_it="Pompa di calore",
        description_en="Heat pump",
    )
    config = _configured(
        db_session, dealership.id, variant,
        [
            ConfigurationOptionInput(variant_option_id=pump.id, description="Wärmepumpe", option_code="HP1", price=Decimal("1250.00")),
            ConfigurationOptionInput(description="Anhängerkupplung", option_code="AHK", price=Decimal("900.00")),
        ],
    )
    offer = create_offer(db_session, tenant_id=dealership.id, actor_id=uuid.uuid4())
    offer = update_offer(
        db_session, offer=offer, group_id=uuid.uuid4(), data=OfferUpdate(configuration_id=config.id), actor_id=None
    )

    frozen = db_session.query(SalesLineItem).filter_by(offer_id=offer.id, code="HP1").one()
    assert (frozen.label, frozen.label_fr, frozen.label_en) == ("Wärmepumpe", "Pompe à chaleur", "Heat pump")
    content = build_offer_content(
        offer, language=language, vat_rate=dealership.vat_rate,
        option_lines=_included_option_lines(db_session, offer=offer, language=language),
    )
    (block,) = [b for b in content.blocks if isinstance(b, LineItemsBlock)]
    labels = [line.label for line in block.lines]
    assert labels[1:3] == expected  # the hand-typed option keeps its one text in every language

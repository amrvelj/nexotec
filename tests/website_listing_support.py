"""KAN-225 test support: a Betrieb whose publishing profile the website
channel accepts, and a car in stock ready to list there."""

from app.platform.organisation.models.dealership import Dealership
from app.platform.organisation.models.publishing_profile import PublishingProfile
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.test_inventory_marketplace_transmission import _make_ready_item
from tests.vin_support import unique_vin


def website_ready_dealership(db_session) -> Dealership:
    """A Betrieb (no location: nothing needs a lead e-mail) with the website
    channel's items filled in its correspondence language (German)."""

    dealership = _make_dealership(db_session)
    db_session.add(
        PublishingProfile(
            dealership_id=dealership.id,
            website_domain="garage-ag.ch",
            is_published=True,
            social_profiles={},
            short_description={"de": "Ihr Garagist in Zürich"},
            long_description={},
            impressum={"de": "Garage AG, Bahnhofstrasse 1, 8001 Zürich"},
            privacy_notice={"de": "Wir bearbeiten Ihre Daten nach revDSG."},
            withdrawal_wording={},
            warranty_text={},
            mfk_statement={},
            price_display_wording={},
            standard_equipment_blurb={},
            trade_in_promotion_text={},
        )
    )
    db_session.commit()
    return dealership


def ready_item(db_session, dealership: Dealership, *, vin: str | None = None):
    return _make_ready_item(db_session, dealership.id, vin=vin or unique_vin())

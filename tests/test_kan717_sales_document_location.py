"""KAN-717 (Anto, 2026-11-15, B): an offer or contract prints for its
responsible seller's primary location; with none, for the seat. The site is
fixed when the document is made, so a reprint prints the same letterhead. The
footer's legal-entity columns stay the seat's (A-27); one renderer (ADR-051)."""

import uuid
from unittest.mock import patch

import pytest

from app.core.object_store import LocalDirectoryObjectStore
from app.platform.identity.models.user import User, UserRole, UserStatus
from app.platform.identity.models.user_location import UserLocation
from app.platform.organisation.models.dealership import Location
from app.platform.public import get_primary_location_id
from app.sales.services.document import generate_contract_document, generate_offer_document, render_document_pdf
from tests.test_sales_documents import _bare_contract, _bare_offer, _make_dealership


def _seller(db_session, tenant_id: uuid.UUID) -> User:
    user = User(
        tenant_id=tenant_id, first_name="Vera", last_name="Verkauf", email=f"{uuid.uuid4().hex}@example.test",
        role=UserRole.SALES, access_roles=[], is_dealer_manager=False, status=UserStatus.ACTIVE,
        auth_identity_id=str(uuid.uuid4()),
    )
    db_session.add(user)
    db_session.commit()
    return user


def _site(db_session, tenant_id: uuid.UUID, name: str, *, active: bool = True, address: str | None = None) -> Location:
    site = Location(tenant_id=tenant_id, name=name, is_active=active, letterhead_address_override=address)
    db_session.add(site)
    db_session.commit()
    return site


def _works_at(db_session, user: User, site: Location, *, primary: bool) -> None:
    db_session.add(UserLocation(user_id=user.id, location_id=site.id, is_primary=primary))
    db_session.commit()


def _document(db_session, kind: str, *, tenant_id: uuid.UUID, seller_id: uuid.UUID | None):
    if kind == "offer":
        offer = _bare_offer(tenant_id=tenant_id, owner_user_id=seller_id, owner_label="Vera Verkauf")
        db_session.add(offer)
        db_session.commit()
        return generate_offer_document(db_session, offer=offer, actor_id=None)
    contract = _bare_contract(tenant_id=tenant_id, owner_user_id=seller_id, owner_label="Vera Verkauf")
    db_session.add(contract)
    db_session.commit()
    return generate_contract_document(db_session, contract=contract, actor_id=None)


def _render_location(db_session, document) -> uuid.UUID | None:
    with patch("app.sales.services.document.render_document", return_value=b"%PDF-stub") as render:
        render_document_pdf(db_session, document=document, dealership_id=document.tenant_id)
    return render.call_args.kwargs["location_id"]


@pytest.mark.parametrize("kind", ["offer", "contract"])
def test_the_document_prints_for_the_sellers_primary_location(db_session, kind):
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id)
    other = _site(db_session, dealership.id, "Filiale Baden")
    primary = _site(db_session, dealership.id, "Filiale Winterthur")
    _works_at(db_session, seller, other, primary=False)
    _works_at(db_session, seller, primary, primary=True)

    document = _document(db_session, kind, tenant_id=dealership.id, seller_id=seller.id)

    assert document.location_id == primary.id
    assert _render_location(db_session, document) == primary.id


@pytest.mark.parametrize("kind", ["offer", "contract"])
def test_without_a_seller_the_document_prints_for_the_seat(db_session, kind):
    dealership = _make_dealership(db_session)
    document = _document(db_session, kind, tenant_id=dealership.id, seller_id=None)
    assert document.location_id is None
    assert _render_location(db_session, document) is None


@pytest.mark.parametrize("kind", ["offer", "contract"])
def test_a_seller_without_a_primary_location_prints_for_the_seat(db_session, kind):
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id)
    _works_at(db_session, seller, _site(db_session, dealership.id, "Filiale Baden"), primary=False)
    document = _document(db_session, kind, tenant_id=dealership.id, seller_id=seller.id)
    assert document.location_id is None
    assert _render_location(db_session, document) is None


def test_a_deactivated_primary_location_prints_for_the_seat(db_session):
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id)
    _works_at(db_session, seller, _site(db_session, dealership.id, "Alte Filiale", active=False), primary=True)
    assert _document(db_session, "offer", tenant_id=dealership.id, seller_id=seller.id).location_id is None


def test_a_seller_homed_in_another_betrieb_prints_this_betriebs_seat(db_session):
    # A member acting here keeps the primary site of their own Betrieb; that site
    # never prints on this Betrieb's documents.
    home = _make_dealership(db_session)
    here = _make_dealership(db_session)
    seller = _seller(db_session, home.id)
    _works_at(db_session, seller, _site(db_session, home.id, "Filiale anderswo"), primary=True)

    assert get_primary_location_id(db_session, dealership_id=here.id, user_id=seller.id) is None
    assert _document(db_session, "offer", tenant_id=here.id, seller_id=seller.id).location_id is None


def test_a_reprint_keeps_the_site_the_document_was_made_for(db_session):
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id)
    first = _site(db_session, dealership.id, "Filiale Winterthur")
    _works_at(db_session, seller, first, primary=True)
    document = _document(db_session, "contract", tenant_id=dealership.id, seller_id=seller.id)

    # The seller moves to another showroom after the document was made.
    db_session.query(UserLocation).filter(UserLocation.user_id == seller.id).delete()
    db_session.commit()
    _works_at(db_session, seller, _site(db_session, dealership.id, "Filiale Baden"), primary=True)

    assert _render_location(db_session, document) == first.id


def test_the_printed_letterhead_is_the_sites_and_the_footer_stays_the_seats(db_session, tmp_path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    dealership = _make_dealership(db_session)
    seller = _seller(db_session, dealership.id)
    site = _site(db_session, dealership.id, "Filiale Winterthur", address="Garage AG\nFiliale Winterthur\nTechnikumstrasse 9")
    _works_at(db_session, seller, site, primary=True)
    document = _document(db_session, "offer", tenant_id=dealership.id, seller_id=seller.id)

    with (
        patch("app.platform.documents.services.document_render.get_object_store", return_value=store),
        patch("app.platform.documents.services.document_render.write_pdf", return_value=b"%PDF-stub") as write_pdf,
    ):
        render_document_pdf(db_session, document=document, dealership_id=document.tenant_id)
    (html_string,) = write_pdf.call_args.args

    assert "Technikumstrasse 9" in html_string
    # The legal-entity footer stays the seat's (A-27).
    assert "Bahnhofstrasse" in html_string

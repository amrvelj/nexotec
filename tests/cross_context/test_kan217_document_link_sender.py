"""KAN-217 PR-7 (ADR-208; Anto, 2026-11-20, option 1): the customer's link
page is the dealer's page. The Betrieb's name and logo are read by link only:
a link that cannot be read names nobody; a usable link whose document then
fails still names its sender.
"""

import datetime as dt
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.base import utcnow
from app.core.object_store import LocalDirectoryObjectStore
from app.platform.documents.models.document_link import OPENS_PER_WINDOW, DocumentLink
from app.platform.documents.services import document_links
from app.platform.organisation.models.branding_asset import BrandingAssetKind
from app.platform.organisation.models.dealership import DealershipStatus
from app.platform.organisation.services.branding import set_asset
from app.sales.models.document import SalesDocument
from tests.cross_context.test_kan217_document_links import _dealership, _issue, _offer_document

_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
    b'<rect width="120" height="40" fill="#123456"/></svg>'
)


@pytest.fixture
def store(tmp_path):
    store = LocalDirectoryObjectStore(tmp_path)
    with patch("app.platform.documents.services.document_links.get_object_store", return_value=store):
        yield store


def _with_logo(db, dealership, store):
    set_asset(db, store, dealership=dealership, kind=BrandingAssetKind.PRIMARY, data=_SVG, actor_id=None)


def _sender(client, token: str):
    return client.get("/v1/document-links/current/sender", headers={"X-Document-Link": token})


def _logo(client, token: str):
    return client.get("/v1/document-links/current/logo", headers={"X-Document-Link": token})


def _row(db, link_id):
    db.expire_all()
    return db.scalar(select(DocumentLink).where(DocumentLink.id == link_id))


def test_a_link_names_its_betrieb_by_trade_name_and_serves_its_logo(client, db_session, store):
    dealership = _dealership(db_session, "Garage Muster AG")
    dealership.trade_name = "Muster Automobile"
    db_session.commit()
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)

    sender = _sender(client, issued.token)
    logo = _logo(client, issued.token)

    assert sender.status_code == 200
    assert sender.json() == {"name": "Muster Automobile", "hasLogo": True}
    assert sender.headers["cache-control"] == "no-store, private"
    assert sender.headers["referrer-policy"] == "no-referrer"
    assert logo.status_code == 200
    assert b"svg" in logo.content and b"#123456" in logo.content
    assert logo.headers["content-type"].startswith("image/svg+xml")
    assert logo.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in logo.headers["content-security-policy"]
    assert logo.headers["cache-control"] == "no-store, private"


def test_without_a_trade_name_or_a_logo_the_legal_name_alone(client, db_session, store):
    dealership = _dealership(db_session, "Garage Muster AG")
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)

    assert _sender(client, issued.token).json() == {"name": "Garage Muster AG", "hasLogo": False}
    assert _logo(client, issued.token).status_code == 404


def test_reading_the_sender_writes_nothing_and_counts_no_open(client, db_session, store):
    dealership = _dealership(db_session)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)

    for _ in range(OPENS_PER_WINDOW + 5):
        assert _sender(client, issued.token).status_code == 200

    row = _row(db_session, issued.link_id)
    assert row.open_count == 0 and row.window_opens == 0 and row.first_opened_at is None


@pytest.mark.parametrize("token", ["", "short", "A" * 43, "A" * 42 + "!"])
def test_an_unknown_or_malformed_link_names_nobody(client, token):
    assert _sender(client, token).status_code == 404
    assert _logo(client, token).status_code == 404


def test_an_expired_link_names_nobody(client, db_session, store):
    dealership = _dealership(db_session)
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)
    row = _row(db_session, issued.link_id)
    row.expires_at = utcnow() - dt.timedelta(seconds=1)
    db_session.commit()

    assert _sender(client, issued.token).status_code == 404
    assert _logo(client, issued.token).status_code == 404


def test_an_offboarded_betriebs_link_names_nobody(client, db_session, store):
    dealership = _dealership(db_session)
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)
    dealership.status = DealershipStatus.OFFBOARDED
    db_session.commit()

    assert _sender(client, issued.token).status_code == 404
    assert _logo(client, issued.token).status_code == 404


def test_a_used_up_link_names_nobody(client, db_session, store):
    dealership = _dealership(db_session)
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)
    row = _row(db_session, issued.link_id)
    row.window_started_at = utcnow()
    row.window_opens = OPENS_PER_WINDOW
    db_session.commit()

    assert _sender(client, issued.token).status_code == 404
    assert _logo(client, issued.token).status_code == 404


def test_the_logo_claims_one_read_of_the_window(client, db_session, store):
    dealership = _dealership(db_session)
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)

    for _ in range(OPENS_PER_WINDOW):
        assert _logo(client, issued.token).status_code == 200
    assert _logo(client, issued.token).status_code == 404

    row = _row(db_session, issued.link_id)
    assert row.window_opens == OPENS_PER_WINDOW
    # The logo is not the document: nothing was opened.
    assert row.open_count == 0


def test_a_link_whose_document_is_gone_still_names_its_sender(client, db_session, store):
    """Anto's «a valid link that later fails names the dealer»: the page
    then says «Wenden Sie sich an Garage Muster AG»."""

    dealership = _dealership(db_session, "Garage Muster AG")
    document = _offer_document(db_session, dealership)
    issued = _issue(db_session, dealership, document.id)
    db_session.delete(db_session.get(SalesDocument, document.id))
    db_session.commit()

    assert client.get("/v1/document-links/current", headers={"X-Document-Link": issued.token}).status_code == 404
    assert _sender(client, issued.token).json() == {"name": "Garage Muster AG", "hasLogo": False}


def test_a_link_names_only_its_own_betrieb(client, db_session, store):
    ours = _dealership(db_session, "Garage Muster AG")
    theirs = _dealership(db_session, "Autohaus Anders AG")
    _with_logo(db_session, theirs, store)
    issued = _issue(db_session, ours, _offer_document(db_session, ours).id)

    assert _sender(client, issued.token).json() == {"name": "Garage Muster AG", "hasLogo": False}
    assert _logo(client, issued.token).status_code == 404


def test_a_logo_missing_from_the_store_is_404_and_logged(client, db_session, store, caplog):
    dealership = _dealership(db_session)
    _with_logo(db_session, dealership, store)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)
    asset = document_links.resolve_asset(db_session, dealership.id, BrandingAssetKind.PRIMARY)
    store.delete(asset.object_key)

    with caplog.at_level("ERROR"):
        assert _logo(client, issued.token).status_code == 404
    assert issued.token not in caplog.text
    assert "not in the object store" in caplog.text


def test_the_token_never_reaches_a_log_line(client, db_session, store, caplog):
    dealership = _dealership(db_session)
    issued = _issue(db_session, dealership, _offer_document(db_session, dealership).id)

    with caplog.at_level("DEBUG"):
        _sender(client, issued.token)
        _logo(client, issued.token)
        _sender(client, "B" * 43)

    assert issued.token not in caplog.text
    assert "B" * 43 not in caplog.text

"""KAN-225 — a car on the Betrieb's website, and the public stock projection
the website reads (D-A-26, FR-A-05, FR-A-27, ADR-029).

What is pinned here:
- the allow-list: Stock's snapshot and reporting's projection name the same
  fields, none of them a cost, margin, purchase, valuation, VIN or internal
  field — and a field smuggled into a statement never reaches the
  projection;
- listing refused before it exists: the listing conditions and the
  publishing profile's missing items, each named;
- every change of a public field restates the listing in its transaction; a
  change nothing public sees states nothing; a reservation shows; a car that
  left stock leaves the website; offboarding withdraws it;
- the consumer applies a statement only when newer (revision);
- the routes: If-Match, the matrix row, the projection's read;
- the nightly reconciliation finds a statement that never arrived.
"""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.auth import AccessRole
from app.core.errors import ConflictError
from app.core.outbox_model import OutboxMessage
from app.core.reconciliation import find_rows_in_state
from app.inventory.schemas.stock_item import StockItemUpdate
from app.inventory.services import website_listing
from app.inventory.services.invoicing_gate import apply_finance_invoice_issued
from app.inventory.services.listing_withdrawal import withdraw_live_listings
from app.inventory.services.publishing import add_media
from app.inventory.services.reservation import release_and_flush, reserve_and_flush
from app.inventory.services.stock_item import update_stock_item
from app.platform.organisation.models.publishing_profile import PublishingProfile
from app.reporting import reconciliation as reporting_reconciliation
from app.reporting.consumers import handle_website_listing_changed_message
from app.reporting.models.public_stock_listing import PublicStockListing
from app.reporting.public import PUBLIC_STOCK_FIELDS, count_public_stock, list_public_stock
from app.reporting.services.public_stock import apply_listing_changed
from tests.test_dealership import _bearer, _token
from tests.test_inventory_invoicing_gate import _make_dealership
from tests.website_listing_support import ready_item, website_ready_dealership

_EVENT = website_listing.EVENT_TYPE

# Never on the public website (ADR-029) — by name, in any spelling.
_FORBIDDEN_FRAGMENTS = (
    "cost",
    "margin",
    "purchase",
    "landed",
    "valuation",
    "notional",
    "supplier",
    "vin",
    "wagenbuch",
    "ledger",
    "haendler",
    "invoice",
    "base",
    "listprice",
)


def _statements(db_session, item_id) -> list[OutboxMessage]:
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == _EVENT, OutboxMessage.aggregate_id == item_id)
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
        ).all()
    )


def _deliver_all(db_session, item_id) -> None:
    for message in _statements(db_session, item_id):
        handle_website_listing_changed_message(db_session, message)
    db_session.commit()


def _listed(db_session, dealership_id):
    return list_public_stock(db_session, dealership_id=dealership_id)


# --- the allow-list ------------------------------------------------------------------------


def test_stock_and_the_projection_name_the_same_public_fields():
    assert set(website_listing.WEBSITE_LISTING_FIELDS) == PUBLIC_STOCK_FIELDS


@pytest.mark.parametrize("field", sorted(PUBLIC_STOCK_FIELDS))
def test_no_public_field_is_a_private_figure(field):
    assert not any(fragment in field.lower() for fragment in _FORBIDDEN_FRAGMENTS), field


def test_a_field_smuggled_into_a_statement_never_reaches_the_projection(db_session):
    dealership = _make_dealership(db_session)
    stock_item_id = uuid.uuid4()
    applied = apply_listing_changed(
        db_session,
        tenant_id=dealership.id,
        payload={
            "stockItemId": str(stock_item_id),
            "listed": True,
            "revision": 1,
            "locationId": None,
            "listedAt": dt.datetime.now(dt.UTC).isoformat(),
            "listing": {"priceChf": "19900.00", "purchasePrice": "12000.00", "landedCost": "12500.00", "vin": "X"},
        },
    )
    db_session.commit()
    assert applied
    [item] = _listed(db_session, dealership.id)
    assert item.listing == {"priceChf": "19900.00"}
    stored = db_session.scalar(select(PublicStockListing).where(PublicStockListing.stock_item_id == stock_item_id))
    assert stored.listing == {"priceChf": "19900.00"}


def test_the_snapshot_holds_exactly_the_allow_listed_fields(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    snapshot = website_listing.public_snapshot(db_session, item)
    assert tuple(snapshot) == website_listing.WEBSITE_LISTING_FIELDS
    assert snapshot["priceChf"] == "19900.00"
    assert snapshot["imageUrls"] == ["https://example.com/photo1.jpg"]
    assert snapshot["reserved"] is False


# --- refused before it exists (FR-A-05) ----------------------------------------------------


def test_an_incomplete_profile_refuses_the_website_naming_each_item(db_session):
    dealership = _make_dealership(db_session)  # no publishing profile at all
    item = ready_item(db_session, dealership)
    with pytest.raises(ConflictError) as refused:
        website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    codes = [c["code"] for c in refused.value.details["blockingConditions"]]
    assert codes == ["website_domain_missing", "short_description_missing", "impressum_missing", "privacy_notice_missing"]
    assert _statements(db_session, item.id) == []


def test_the_listing_conditions_refuse_the_website_too(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    update_stock_item(db_session, item=item, data=StockItemUpdate(effective_price=None), actor_id=uuid.uuid4())
    with pytest.raises(ConflictError) as refused:
        website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    assert [c["field"] for c in refused.value.details["blockingConditions"]] == ["Preis"]


# --- stated, kept current, withdrawn --------------------------------------------------------


def test_listing_states_the_snapshot_and_the_projection_shows_it(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    row = website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    assert row.is_listed and row.revision == 1
    [statement] = _statements(db_session, item.id)
    assert statement.payload["listed"] is True
    assert set(statement.payload["listing"]) == PUBLIC_STOCK_FIELDS

    _deliver_all(db_session, item.id)
    [public] = _listed(db_session, dealership.id)
    assert public.stock_item_id == item.id
    assert public.listing["vehicleLabel"] == "Škoda Octavia"
    assert public.published_at is not None
    assert count_public_stock(db_session, dealership_id=dealership.id) == 1


def test_a_price_change_restates_and_a_change_nothing_public_sees_states_nothing(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())

    update_stock_item(db_session, item=item, data=StockItemUpdate(effective_price=Decimal("18900.00")), actor_id=None)
    update_stock_item(db_session, item=item, data=StockItemUpdate(list_price=Decimal("31000.00")), actor_id=None)
    statements = _statements(db_session, item.id)
    assert [s.payload["revision"] for s in statements] == [1, 2]  # the list price is not public
    assert statements[1].payload["listing"]["priceChf"] == "18900.00"

    add_media(db_session, item=item, url="https://example.com/photo2.jpg")
    assert _statements(db_session, item.id)[-1].payload["listing"]["imageUrls"][-1] == "https://example.com/photo2.jpg"

    _deliver_all(db_session, item.id)
    [public] = _listed(db_session, dealership.id)
    assert public.listing["priceChf"] == "18900.00"
    assert len(public.listing["imageUrls"]) == 2


def test_a_reservation_shows_and_its_release_clears_it(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    reserve_and_flush(db_session, item=item, contract_id=uuid.uuid4())
    db_session.commit()
    assert _statements(db_session, item.id)[-1].payload["listing"]["reserved"] is True
    release_and_flush(db_session, item=item)
    db_session.commit()
    assert _statements(db_session, item.id)[-1].payload["listing"]["reserved"] is False


def test_a_car_that_left_stock_leaves_the_website(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    item.is_invoiceable = True
    db_session.commit()
    apply_finance_invoice_issued(db_session, tenant_id=dealership.id, stock_item_id=item.id, invoice_ref="R-1")
    last = _statements(db_session, item.id)[-1]
    assert last.payload["listed"] is False and last.payload["listing"] is None
    _deliver_all(db_session, item.id)
    assert _listed(db_session, dealership.id) == []


def test_unlisting_takes_it_off_and_an_older_statement_cannot_put_it_back(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    website_listing.unlist_from_website(db_session, item=item, actor_id=uuid.uuid4())
    listed, unlisted = _statements(db_session, item.id)

    handle_website_listing_changed_message(db_session, unlisted)  # delivered out of order
    handle_website_listing_changed_message(db_session, listed)
    db_session.commit()
    assert _listed(db_session, dealership.id) == []
    stored = db_session.scalar(select(PublicStockListing).where(PublicStockListing.stock_item_id == item.id))
    assert stored.revision == 2 and stored.is_listed is False


def test_another_betriebs_statement_never_overwrites_a_row(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    _deliver_all(db_session, item.id)
    applied = apply_listing_changed(
        db_session,
        tenant_id=uuid.uuid4(),
        payload={"stockItemId": str(item.id), "listed": False, "revision": 99, "locationId": None, "listing": None},
    )
    assert applied is False


def test_offboarding_withdraws_the_website_listings(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    withdrawn = withdraw_live_listings(
        db_session, tenant_id=dealership.id, causation_id=uuid.uuid4(), correlation_id=uuid.uuid4()
    )
    db_session.commit()
    assert withdrawn == 1
    assert _statements(db_session, item.id)[-1].payload["listed"] is False
    assert website_listing.count_listed(db_session, tenant_id=dealership.id) == 0


# --- the routes ------------------------------------------------------------------------------


def _headers(dealership_id, role=AccessRole.INVENTORY, version=None):
    headers = _bearer(_token(role, tenant_id=dealership_id))
    if version is not None:
        headers["If-Match"] = str(version)
    return headers


def test_the_website_routes(client, db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    url = f"/v1/inventory/stock-items/{item.id}/website-listing"

    body = client.get(url, headers=_headers(dealership.id)).json()
    assert body["isListed"] is False and body["version"] == 1 and body["blockingConditions"] == []

    assert client.post(f"{url}/publish", headers=_headers(dealership.id)).status_code == 400  # If-Match required
    refused = client.post(f"{url}/publish", headers=_headers(dealership.id, AccessRole.SALES, 1))
    assert refused.status_code == 403  # the matrix row marketplace_publishing
    listed = client.post(f"{url}/publish", headers=_headers(dealership.id, version=1))
    assert listed.status_code == 200, listed.text
    assert listed.json()["isListed"] is True and listed.json()["version"] == 2
    stale = client.post(f"{url}/unpublish", headers=_headers(dealership.id, version=1))
    assert stale.status_code == 409
    off = client.post(f"{url}/unpublish", headers=_headers(dealership.id, version=2))
    assert off.status_code == 200 and off.json()["isListed"] is False

    other = _make_dealership(db_session)
    assert client.get(url, headers=_headers(other.id)).status_code == 404


def test_the_profile_items_reach_the_stock_screen_as_codes(client, db_session):
    dealership = website_ready_dealership(db_session)
    profile = db_session.scalar(select(PublishingProfile).where(PublishingProfile.dealership_id == dealership.id))
    profile.impressum = {}
    db_session.commit()
    item = ready_item(db_session, dealership)
    body = client.get(f"/v1/inventory/stock-items/{item.id}/website-listing", headers=_headers(dealership.id)).json()
    assert body["blockingConditions"] == [{"field": "publishingProfile", "message": "", "code": "impressum_missing"}]


def test_the_projection_read(client, db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    _deliver_all(db_session, item.id)
    response = client.get(f"/v1/dealerships/{dealership.id}/public-stock", headers=_headers(dealership.id, AccessRole.SALES))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    [car] = body["items"]
    assert car["priceChf"] == "19900.00" and car["vehicleLabel"] == "Škoda Octavia"
    assert car["imageUrls"] == ["https://example.com/photo1.jpg"]
    assert not any(fragment in key.lower() for key in car for fragment in ("cost", "margin", "purchase", "valuation"))
    other = _make_dealership(db_session)
    assert client.get(f"/v1/dealerships/{dealership.id}/public-stock", headers=_headers(other.id)).status_code == 404


# --- reconciliation (rule 10) ----------------------------------------------------------------


def _findings(db_session, label_part):
    [check] = [c for c in reporting_reconciliation.CHECKS if label_part in c.label]
    return find_rows_in_state(db_session, check)


def test_a_statement_that_never_arrived_is_reported_after_the_grace(db_session):
    dealership = website_ready_dealership(db_session)
    item = ready_item(db_session, dealership)
    row = website_listing.list_on_website(db_session, item=item, actor_id=uuid.uuid4())
    assert _findings(db_session, "no public stock row") == []  # in flight
    row.updated_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    db_session.commit()
    assert [found for found, _ in _findings(db_session, "no public stock row")] == [row.id]
    _deliver_all(db_session, item.id)
    assert _findings(db_session, "no public stock row") == []


def test_a_projection_row_stock_does_not_list_is_reported(db_session):
    dealership = _make_dealership(db_session)
    apply_listing_changed(
        db_session,
        tenant_id=dealership.id,
        payload={"stockItemId": str(uuid.uuid4()), "listed": True, "revision": 1, "locationId": None, "listing": {}},
    )
    db_session.commit()
    stored = db_session.scalar(select(PublicStockListing))
    stored.updated_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    db_session.commit()
    assert [found for found, _ in _findings(db_session, "does not list")] == [stored.id]

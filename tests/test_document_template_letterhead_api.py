"""KAN-206 over HTTP: the template's letterhead fields, the preview through
the one renderer, a location's own letterhead logo and the Betrieb's e-mail
and website (Dealer Administration PRD, "Letterhead and the document
template"; Anto's answers of 2026-11-12)."""

import uuid
from unittest.mock import patch

import pytest

from app.core.auth import AccessRole
from app.platform.schemas.document_content import DocumentWatermark
from app.platform.services import document_render
from tests.test_branding import _SVG, _audit, _manager, _role, object_store  # noqa: F401  (fixture)
from tests.test_dealership import _bearer, _create_dealership, _token

_LAYOUT_DEFAULTS = {
    "headerLayout": "logo_left",
    "logoSize": "m",
    "addressWindow": "right",
    "footerColumns": ["company", "contact", "banking", "register"],
    "draftWatermark": True,
    "pageNumbers": True,
}


def _template(client, dealership_id: str, headers=None) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}/document-template", headers=headers or _manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()


def _patch(client, dealership_id: str, body: dict, *, version: int, headers=None):
    return client.patch(
        f"/v1/dealerships/{dealership_id}/document-template",
        json=body,
        headers={**(headers or _manager(dealership_id)), "If-Match": str(version)},
    )


def _first_location(client, dealership_id: str) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}/locations", headers=_manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


# --- the template's letterhead fields ---------------------------------------------------


def test_a_betrieb_without_a_template_reads_the_shipped_layout(client):
    dealership = _create_dealership(client)
    body = _template(client, dealership["id"], headers=_role(dealership["id"], AccessRole.SALES))
    assert body["version"] == 0
    assert {key: body[key] for key in _LAYOUT_DEFAULTS} == _LAYOUT_DEFAULTS


def test_the_layout_is_saved_with_if_match_and_audited(client):
    dealership = _create_dealership(client)
    did = dealership["id"]
    created = _patch(client, did, {"addressWindow": "left", "logoSize": "l"}, version=0)
    assert created.status_code == 200, created.text
    assert created.json()["addressWindow"] == "left"
    assert created.json()["logoSize"] == "l"
    assert created.json()["headerLayout"] == "logo_left"  # the rest keeps the shipped defaults

    stale = _patch(client, did, {"headerLayout": "logo_centred"}, version=0)
    assert stale.status_code == 409

    updated = _patch(
        client,
        did,
        {"headerLayout": "logo_centred", "footerColumns": ["register", "company"], "draftWatermark": False,
         "pageNumbers": False},
        version=1,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["version"] == 2
    assert body["footerColumns"] == ["register", "company"]
    assert body["draftWatermark"] is False and body["pageNumbers"] is False

    events = [e for e in _audit(client, did) if e["entityType"] == "document_template"]
    update = next(e for e in events if e["action"] == "update")
    assert update["before"]["header_layout"] == "logo_left"
    assert update["after"]["header_layout"] == "logo_centred"
    assert update["after"]["footer_columns"] == ["register", "company"]
    create = next(e for e in events if e["action"] == "create")
    assert create["after"]["address_window"] == "left"


@pytest.mark.parametrize(
    "body",
    [
        {"footerColumns": ["company", "contact", "banking", "register", "company"]},  # five
        {"footerColumns": ["company", "company"]},  # twice
        {"footerColumns": ["contact", "banking"]},  # the legal entity left out (A-27)
        {"footerColumns": []},
        {"footerColumns": ["company", "fax"]},
        {"addressWindow": "middle"},
        {"logoSize": "xl"},
        {"headerLayout": None},
        {"pageNumbers": None},
    ],
)
def test_an_invalid_layout_is_refused(client, body):
    dealership = _create_dealership(client)
    response = _patch(client, dealership["id"], body, version=0)
    assert response.status_code == 422, response.text


def test_only_the_manager_writes_the_layout(client):
    dealership = _create_dealership(client)
    response = _patch(client, dealership["id"], {"addressWindow": "left"}, version=0,
                      headers=_role(dealership["id"], AccessRole.SALES))
    assert response.status_code == 403


# --- the preview, through the one renderer ----------------------------------------------


@pytest.mark.parametrize("document", ["offer", "invoice"])
def test_the_preview_is_a_sample_pdf_through_render_document(client, object_store, document):  # noqa: F811
    dealership = _create_dealership(client)
    did = dealership["id"]
    location = _first_location(client, did)
    real = document_render.render_document
    with patch("app.platform.api.document_template.render_document", wraps=real) as render:
        response = client.get(
            f"/v1/dealerships/{did}/document-template/preview",
            params={"document": document, "location": location["id"]},
            headers=_manager(did),
        )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"%PDF")
    (call,) = render.call_args_list
    assert call.kwargs["watermark"] is DocumentWatermark.SAMPLE
    assert call.kwargs["location_id"] == uuid.UUID(location["id"])
    assert call.kwargs["dealership_id"] == uuid.UUID(did)


def test_the_preview_prints_the_saved_template_and_the_sample(client, object_store):  # noqa: F811
    dealership = _create_dealership(client)
    did = dealership["id"]
    _patch(client, did, {"addressWindow": "left", "headerLayout": "logo_right"}, version=0)
    with patch("app.platform.services.document_render.write_pdf", return_value=b"%PDF-stub") as write_pdf:
        response = client.get(
            f"/v1/dealerships/{did}/document-template/preview", params={"document": "invoice"}, headers=_manager(did)
        )
    assert response.status_code == 200, response.text
    (html_string,) = write_pdf.call_args.args
    assert '<div class="doc-watermark">MUSTER</div>' in html_string
    assert 'class="doc-head logo_right"' in html_string
    assert "left:-5.00mm" in html_string  # the left window: 15 mm from the sheet's edge, 20 mm margin
    assert "Rechnung 2026-0001" in html_string


@pytest.mark.parametrize("role", [AccessRole.SALES, AccessRole.FINANCE, AccessRole.AUDITOR])
def test_only_those_who_edit_the_template_may_preview_it(client, role):
    """The footer prints the Betrieb's real IBAN: readers of the template are
    refused the preview."""

    dealership = _create_dealership(client)
    response = client.get(
        f"/v1/dealerships/{dealership['id']}/document-template/preview", headers=_role(dealership["id"], role)
    )
    assert response.status_code == 403


def test_the_preview_of_another_betrieb_or_its_site_is_a_404(client):
    ours = _create_dealership(client)
    theirs = _create_dealership(client)
    foreign_site = _first_location(client, theirs["id"])

    other = client.get(f"/v1/dealerships/{theirs['id']}/document-template/preview", headers=_manager(ours["id"]))
    assert other.status_code == 404
    site = client.get(
        f"/v1/dealerships/{ours['id']}/document-template/preview",
        params={"location": foreign_site["id"]},
        headers=_manager(ours["id"]),
    )
    assert site.status_code == 404


def test_an_unknown_sample_is_refused(client):
    dealership = _create_dealership(client)
    response = client.get(
        f"/v1/dealerships/{dealership['id']}/document-template/preview",
        params={"document": "contract"},
        headers=_manager(dealership["id"]),
    )
    assert response.status_code == 422


# --- a location's own letterhead logo -----------------------------------------------------


def _put_location_logo(client, did: str, location_id: str, *, version: int, headers=None):
    return client.put(
        f"/v1/dealerships/{did}/branding/locations/{location_id}/logo",
        content=_SVG,
        headers={**(headers or _manager(did)), "If-Match": str(version), "Content-Type": "image/svg+xml"},
    )


def test_a_location_gets_its_own_letterhead_logo(client, object_store):  # noqa: F811
    dealership = _create_dealership(client)
    did = dealership["id"]
    location = _first_location(client, did)

    response = _put_location_logo(client, did, location["id"], version=dealership["version"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert [logo["locationId"] for logo in body["locationLogos"]] == [location["id"]]
    # The Betrieb's own variants are untouched: it still has no logo of its own.
    assert {asset["kind"]: asset["source"] for asset in body["assets"]}["primary"] == "none"

    served = client.get(f"/v1/dealerships/{did}/branding/locations/{location['id']}/logo",
                        headers=_role(did, AccessRole.SALES))
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert served.headers["cache-control"] == "private, no-store"

    event = next(e for e in _audit(client, did) if e["action"] == "branding_asset_set")
    assert event["after"]["location_id"] == location["id"]

    removed = client.delete(
        f"/v1/dealerships/{did}/branding/locations/{location['id']}/logo",
        headers={**_manager(did), "If-Match": str(body["version"])},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["locationLogos"] == []
    gone = client.get(f"/v1/dealerships/{did}/branding/locations/{location['id']}/logo", headers=_manager(did))
    assert gone.status_code == 404


def test_a_location_logo_needs_the_manager_the_current_version_and_its_own_betrieb(client, object_store):  # noqa: F811
    ours = _create_dealership(client)
    theirs = _create_dealership(client)
    location = _first_location(client, ours["id"])
    foreign = _first_location(client, theirs["id"])

    assert _put_location_logo(client, ours["id"], location["id"], version=ours["version"],
                              headers=_role(ours["id"], AccessRole.SALES)).status_code == 403
    assert _put_location_logo(client, ours["id"], location["id"], version=ours["version"] + 5).status_code == 409
    assert _put_location_logo(client, ours["id"], foreign["id"], version=ours["version"]).status_code == 404
    assert _put_location_logo(client, theirs["id"], foreign["id"], version=theirs["version"],
                              headers=_manager(ours["id"])).status_code == 404


def test_the_betriebs_logo_and_a_location_logo_live_side_by_side(client, object_store):  # noqa: F811
    from tests.test_branding import _upload

    dealership = _create_dealership(client)
    did = dealership["id"]
    location = _first_location(client, did)
    version = _upload(client, did, "primary", _SVG, version=dealership["version"], content_type="image/svg+xml").json()[
        "version"
    ]
    response = _put_location_logo(client, did, location["id"], version=version)
    assert response.status_code == 200, response.text
    assert {asset["kind"]: asset["source"] for asset in response.json()["assets"]}["primary"] == "own"
    assert len(response.json()["locationLogos"]) == 1


# --- the Betrieb's e-mail and website (Q4 C) ------------------------------------------------


def test_the_betriebs_email_and_website_are_master_data_and_audited(client):
    body = _create_dealership(client)
    headers = {**_bearer(_token(AccessRole.PLATFORM_ADMIN)), "If-Match": str(body["version"])}
    response = client.patch(
        f"/v1/dealerships/{body['id']}",
        json={"email": "info@garage-muster.ch", "website": "www.garage-muster.ch"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "info@garage-muster.ch"
    assert response.json()["website"] == "www.garage-muster.ch"

    event = next(e for e in _audit(client, body["id"]) if e["action"] == "update" and e["entityType"] == "dealership")
    assert event["after"] == {"email": "info@garage-muster.ch", "website": "www.garage-muster.ch"}
    assert event["before"] == {"email": None, "website": None}

    cleared = client.patch(
        f"/v1/dealerships/{body['id']}",
        json={"website": None},
        headers={**_bearer(_token(AccessRole.PLATFORM_ADMIN)), "If-Match": str(response.json()["version"])},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["website"] is None


@pytest.mark.parametrize(
    "field", [{"email": "not-an-address"}, {"website": "garage muster"}, {"website": "localhost"}]
)
def test_an_invalid_email_or_website_is_refused(client, field):
    body = _create_dealership(client)
    response = client.patch(
        f"/v1/dealerships/{body['id']}",
        json=field,
        headers={**_bearer(_token(AccessRole.PLATFORM_ADMIN)), "If-Match": str(body["version"])},
    )
    assert response.status_code == 422, response.text

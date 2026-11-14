"""KAN-225 — Dealer Administration › Publishing (PRD Publishing profile,
FR-A-04, FR-A-05, FR-A-27, A-RULE-2).

What is pinned here:
- the profile: a read never writes (transient, version 1); the first write
  creates it; one version for the whole screen; localised texts drop blank
  languages and refuse unknown ones; every write audited;
- the matrix row `publishing_profile`: inventory and the manager write, any
  role reads, another Betrieb is a 404;
- per-location overrides inherit unless set (per field, per language); a
  legal notice on an override is refused, never dropped;
- lead routing writes the location's own `lead_email` and moves its version;
- imagery: JPEG or PNG judged by the bytes, ≥ 800 px, ordered, one primary
  that the next image inherits; the logo routes never take an imagery kind;
- the AGB as a PDF in the object store, served private;
- the website channel's validation, in stable codes, and its public read.
"""

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.auth import AccessRole
from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.main import app as fastapi_app
from app.platform.organisation.models.dealership import Location
from app.platform.public import publishing_profile_missing_items, publishing_profile_ready
from tests.test_dealership import _bearer, _create_dealership, _token


def _manager(dealership_id: str) -> dict[str, str]:
    return _bearer(_token(tenant_id=uuid.UUID(dealership_id), is_dealer_manager=True))


def _role(dealership_id: str, role: AccessRole) -> dict[str, str]:
    return _bearer(_token(role, tenant_id=uuid.UUID(dealership_id)))


def _jpeg(width: int = 1200, height: int = 800) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _png(width: int = 1000, height: int = 600) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def object_store(tmp_path: Path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    try:
        yield store
    finally:
        fastapi_app.dependency_overrides.pop(get_object_store, None)


def _url(dealership_id: str, suffix: str = "") -> str:
    return f"/v1/dealerships/{dealership_id}/publishing-profile{suffix}"


def _profile(client, dealership_id: str) -> dict:
    response = client.get(_url(dealership_id), headers=_manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()


def _patch(client, dealership_id: str, body: dict, version: int, headers=None):
    return client.patch(
        _url(dealership_id), json=body, headers={**(headers or _manager(dealership_id)), "If-Match": str(version)}
    )


def _seat(client, dealership_id: str) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}/locations", headers=_manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


# --- the profile ---------------------------------------------------------------------------


def test_a_read_never_writes_and_the_first_write_creates_the_profile(client, db_session):
    dealership = _create_dealership(client, trade_name="Garage Muster")
    body = _profile(client, dealership["id"])
    assert body["version"] == 1
    assert body["tradeName"] == "Garage Muster"
    assert body["shortDescription"] == {} and body["impressum"] == {}
    assert body["isPublished"] is False
    assert body["locationOverrides"] == [] and body["images"] == []
    assert len(body["leadRouting"]) == 1  # the seat location, no routing yet

    response = _patch(
        client,
        dealership["id"],
        {"shortDescription": {"de": "  Ihr Garagist in Zürich  ", "fr": "   "}, "websiteDomain": "HTTPS://Garage-Muster.ch/"},
        1,
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["version"] == 2
    assert saved["shortDescription"] == {"de": "Ihr Garagist in Zürich"}  # the blank language is dropped
    assert saved["websiteDomain"] == "garage-muster.ch"

    audit = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_type == "publishing_profile", AuditEvent.action == "update")
    ).all()
    assert len(audit) == 1
    assert audit[0].after["short_description"] == {"de": "Ihr Garagist in Zürich"}


def test_a_stale_version_is_a_conflict(client):
    dealership = _create_dealership(client)
    assert _patch(client, dealership["id"], {"isPublished": True}, 1).status_code == 200
    stale = _patch(client, dealership["id"], {"isPublished": False}, 1)
    assert stale.status_code == 409, stale.text


@pytest.mark.parametrize(
    "body",
    [
        {"impressum": {"rm": "Rumantsch"}},
        {"websiteDomain": "garage muster"},
        {"agbUrl": "http://insecure.example/agb.pdf"},
        {"socialProfiles": {"myspace": "https://myspace.com/x"}},
        {"isPublished": None},
        {"financingNominalRatePct": "45"},
    ],
)
def test_invalid_fields_are_refused(client, body):
    dealership = _create_dealership(client)
    response = _patch(client, dealership["id"], body, 1)
    assert response.status_code == 422, response.text


def test_the_matrix_row_decides_who_writes(client):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    assert client.get(_url(dealership_id), headers=_role(dealership_id, AccessRole.SALES)).status_code == 200
    refused = _patch(client, dealership_id, {"isPublished": True}, 1, headers=_role(dealership_id, AccessRole.SALES))
    assert refused.status_code == 403
    allowed = _patch(client, dealership_id, {"isPublished": True}, 1, headers=_role(dealership_id, AccessRole.INVENTORY))
    assert allowed.status_code == 200, allowed.text


def test_another_betrieb_is_a_404(client):
    mine = _create_dealership(client)
    other = _create_dealership(client)
    assert client.get(_url(other["id"]), headers=_manager(mine["id"])).status_code == 404
    assert _patch(client, other["id"], {"isPublished": True}, 1, headers=_manager(mine["id"])).status_code == 404


# --- location overrides (FR-A-04) ----------------------------------------------------------


def test_a_location_override_inherits_unless_set(client):
    dealership = _create_dealership(client, trade_name="Garage Muster")
    dealership_id = dealership["id"]
    seat = _seat(client, dealership_id)
    saved = _patch(
        client,
        dealership_id,
        {
            "shortDescription": {"de": "Betrieb DE", "fr": "Betrieb FR"},
            "warrantyText": {"de": "12 Monate Garantie"},
            "deliveryFeeChf": "290.00",
            "financingProvider": "Bank Muster",
        },
        1,
    ).json()

    created = client.post(
        _url(dealership_id, f"/locations/{seat['id']}"),
        json={"shortDescription": {"de": "Standort DE"}, "deliveryFeeChf": "150.00"},
        headers={**_manager(dealership_id), "If-Match": str(saved["version"])},
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == saved["version"] + 1
    [override] = created.json()["locationOverrides"]
    assert override["locationId"] == seat["id"]

    effective = client.get(
        _url(dealership_id, "/effective"), params={"locationId": seat["id"]}, headers=_manager(dealership_id)
    ).json()
    assert effective["shortDescription"] == {"de": "Standort DE", "fr": "Betrieb FR"}  # per language
    assert effective["deliveryFeeChf"] == "150.00"
    assert effective["warrantyText"] == {"de": "12 Monate Garantie"}
    assert effective["financingProvider"] == "Bank Muster"
    assert effective["tradeName"] == "Garage Muster"

    version = created.json()["version"]
    removed = client.delete(
        _url(dealership_id, f"/locations/{seat['id']}"), headers={**_manager(dealership_id), "If-Match": str(version)}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["locationOverrides"] == []
    effective = client.get(
        _url(dealership_id, "/effective"), params={"locationId": seat["id"]}, headers=_manager(dealership_id)
    ).json()
    assert effective["shortDescription"] == {"de": "Betrieb DE", "fr": "Betrieb FR"}
    assert effective["deliveryFeeChf"] == "290.00"


def test_a_legal_notice_on_a_location_override_is_refused(client):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    response = client.post(
        _url(dealership["id"], f"/locations/{seat['id']}"),
        json={"impressum": {"de": "Andere Firma"}},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text


def test_another_betriebs_location_cannot_be_overridden(client):
    mine = _create_dealership(client)
    other = _create_dealership(client)
    foreign = _seat(client, other["id"])
    response = client.post(
        _url(mine["id"], f"/locations/{foreign['id']}"), json={}, headers={**_manager(mine["id"]), "If-Match": "1"}
    )
    assert response.status_code == 404


# --- lead routing ----------------------------------------------------------------------------


def test_lead_routing_writes_the_locations_own_lead_email(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat(client, dealership_id)
    response = client.put(
        _url(dealership_id, f"/locations/{seat['id']}/lead-routing"),
        json={"leadEmail": "verkauf@garage-muster.ch", "leadPhone": "+41 44 000 00 00", "createsTask": True, "recipientRole": "sales"},
        headers={**_manager(dealership_id), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    [routing] = response.json()["leadRouting"]
    assert routing == {
        "locationId": seat["id"],
        "locationName": seat["name"],
        "leadEmail": "verkauf@garage-muster.ch",
        "leadPhone": "+41 44 000 00 00",
        "createsTask": True,
        "recipientUserId": None,
        "recipientRole": "sales",
    }
    location = db_session.get(Location, uuid.UUID(seat["id"]))
    db_session.refresh(location)
    assert location.lead_email == "verkauf@garage-muster.ch"
    assert location.version == seat["version"] + 1  # the Locations screen sees the change


@pytest.mark.parametrize(
    "body",
    [
        {"recipientRole": "sales", "recipientUserId": str(uuid.uuid4())},
        {"recipientRole": "platform_admin"},
        {"leadEmail": "not-an-address"},
    ],
)
def test_lead_routing_refusals(client, body):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    response = client.put(
        _url(dealership["id"], f"/locations/{seat['id']}/lead-routing"),
        json=body,
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text


def test_a_lead_recipient_must_be_a_user_of_this_betrieb(client):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    response = client.put(
        _url(dealership["id"], f"/locations/{seat['id']}/lead-routing"),
        json={"recipientUserId": str(uuid.uuid4())},
        headers={**_manager(dealership["id"]), "If-Match": "1"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "lead_recipient_not_a_user"


# --- imagery ---------------------------------------------------------------------------------


def _add_image(client, dealership_id: str, location_id: str, kind: str, data: bytes, version: int, content_type="image/jpeg"):
    return client.post(
        _url(dealership_id, f"/locations/{location_id}/images/{kind}"),
        content=data,
        headers={**_manager(dealership_id), "If-Match": str(version), "Content-Type": content_type},
    )


def test_imagery_is_ordered_with_one_primary(client, object_store, tmp_path):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat(client, dealership_id)

    first = _add_image(client, dealership_id, seat["id"], "premises", _jpeg(), 1)
    assert first.status_code == 201, first.text
    second = _add_image(client, dealership_id, seat["id"], "premises", _png(), first.json()["version"], "image/png")
    assert second.status_code == 201, second.text
    hero = _add_image(client, dealership_id, seat["id"], "hero", _jpeg(1600, 600), second.json()["version"])
    body = hero.json()
    premises = [i for i in body["images"] if i["kind"] == "premises"]
    assert [i["position"] for i in premises] == [0, 1]
    assert [i["isPrimary"] for i in premises] == [True, False]
    assert premises[1]["mimeType"] == "image/png" and premises[0]["width"] == 1200
    assert [i["isPrimary"] for i in body["images"] if i["kind"] == "hero"] == [True]

    # Reorder, then make the second the primary.
    ids = [premises[1]["id"], premises[0]["id"]]
    reordered = client.put(
        _url(dealership_id, f"/locations/{seat['id']}/images/premises/order"),
        json={"imageIds": ids},
        headers={**_manager(dealership_id), "If-Match": str(body["version"])},
    ).json()
    assert [i["id"] for i in reordered["images"] if i["kind"] == "premises"] == ids
    made_primary = client.post(
        _url(dealership_id, f"/images/{premises[1]['id']}/primary"),
        headers={**_manager(dealership_id), "If-Match": str(reordered["version"])},
    ).json()
    flags = {i["id"]: i["isPrimary"] for i in made_primary["images"] if i["kind"] == "premises"}
    assert flags == {premises[1]["id"]: True, premises[0]["id"]: False}

    # The file is served private; removing the primary hands it to the next.
    served = client.get(_url(dealership_id, f"/images/{premises[1]['id']}"), headers=_manager(dealership_id))
    assert served.status_code == 200 and served.headers["cache-control"] == "private, no-store"
    assert served.content == _png()
    after = client.delete(
        _url(dealership_id, f"/images/{premises[1]['id']}"),
        headers={**_manager(dealership_id), "If-Match": str(made_primary["version"])},
    ).json()
    remaining = [i for i in after["images"] if i["kind"] == "premises"]
    assert [(i["id"], i["position"], i["isPrimary"]) for i in remaining] == [(premises[0]["id"], 0, True)]
    root = tmp_path / "objects"
    keys = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.suffix != ".meta"]
    assert all(k.startswith(f"dealerships/{dealership_id}/publishing/") for k in keys)
    assert len(keys) == 2  # the removed file is gone


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"GIF89a" + b"\x00" * 64, "image_not_an_image"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 4, "image_unreadable"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "image_unreadable"),
    ],
)
def test_imagery_refusals(client, object_store, data, reason):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    response = _add_image(client, dealership["id"], seat["id"], "team", data, 1)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == reason


def test_a_small_image_is_refused_with_its_size(client, object_store):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    response = _add_image(client, dealership["id"], seat["id"], "team", _jpeg(640, 480), 1)
    assert response.status_code == 422
    assert response.json()["error"]["details"]["reason"] == "image_too_small"
    assert response.json()["error"]["details"]["width"] == 640


def test_the_logo_routes_never_take_an_imagery_kind(client, object_store):
    dealership = _create_dealership(client)
    response = client.put(
        f"/v1/dealerships/{dealership['id']}/branding/assets/premises",
        content=_png(),
        headers={**_manager(dealership["id"]), "If-Match": "1", "Content-Type": "image/png"},
    )
    assert response.status_code == 422
    branding = client.get(f"/v1/dealerships/{dealership['id']}/branding", headers=_manager(dealership["id"])).json()
    assert [a["kind"] for a in branding["assets"]] == ["primary", "mono_dark", "favicon"]


def test_imagery_does_not_count_as_a_logo(client, object_store):
    dealership = _create_dealership(client)
    seat = _seat(client, dealership["id"])
    assert _add_image(client, dealership["id"], seat["id"], "premises", _jpeg(), 1).status_code == 201
    branding = client.get(f"/v1/dealerships/{dealership['id']}/branding", headers=_manager(dealership["id"])).json()
    assert {a["source"] for a in branding["assets"]} == {"none"}
    assert branding["locationLogos"] == [] if "locationLogos" in branding else True


# --- AGB --------------------------------------------------------------------------------------


def test_the_agb_is_a_pdf_in_the_object_store(client, object_store):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    refused = client.put(
        _url(dealership_id, "/agb"),
        content=b"plain text",
        headers={**_manager(dealership_id), "If-Match": "1", "Content-Type": "application/pdf"},
    )
    assert refused.status_code == 422 and refused.json()["error"]["details"]["reason"] == "agb_not_a_pdf"

    pdf = b"%PDF-1.7\n% AGB Garage Muster\n%%EOF"
    saved = client.put(
        _url(dealership_id, "/agb"),
        content=pdf,
        headers={**_manager(dealership_id), "If-Match": "1", "Content-Type": "application/pdf"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["agbDocument"]["byteSize"] == len(pdf)
    served = client.get(_url(dealership_id, "/agb"), headers=_role(dealership_id, AccessRole.SALES))
    assert served.status_code == 200 and served.content == pdf
    assert served.headers["content-type"] == "application/pdf"

    removed = client.delete(_url(dealership_id, "/agb"), headers={**_manager(dealership_id), "If-Match": "2"})
    assert removed.status_code == 200 and removed.json()["agbDocument"] is None
    assert client.get(_url(dealership_id, "/agb"), headers=_manager(dealership_id)).status_code == 404


# --- validation for the website channel (FR-A-05) --------------------------------------------


def test_the_website_channel_names_what_is_missing(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = dealership["id"]
    seat = _seat(client, dealership_id)
    body = client.get(_url(dealership_id, "/validation"), headers=_manager(dealership_id)).json()
    assert body["ready"] is False
    assert [m["code"] for m in body["missing"]] == [
        "website_domain_missing",
        "short_description_missing",
        "impressum_missing",
        "privacy_notice_missing",
        "lead_email_missing",
    ]
    assert body["missing"][-1]["locationId"] == seat["id"]
    assert body["missing"][-1]["section"] == "leadRouting"

    # A text in another language than the Betrieb's does not count.
    saved = _patch(
        client,
        dealership_id,
        {
            "websiteDomain": "garage-muster.ch",
            "shortDescription": {"fr": "Votre garage"},
            "impressum": {"de": "Garage Muster AG, Zürich"},
            "privacyNotice": {"de": "Wir bearbeiten Ihre Daten …"},
        },
        1,
    ).json()
    codes = [m["code"] for m in client.get(_url(dealership_id, "/validation"), headers=_manager(dealership_id)).json()["missing"]]
    assert codes == ["short_description_missing", "lead_email_missing"]

    saved = _patch(client, dealership_id, {"shortDescription": {"de": "Ihr Garagist"}}, saved["version"]).json()
    client.put(
        _url(dealership_id, f"/locations/{seat['id']}/lead-routing"),
        json={"leadEmail": "verkauf@garage-muster.ch"},
        headers={**_manager(dealership_id), "If-Match": str(saved["version"])},
    )
    body = client.get(_url(dealership_id, "/validation"), headers=_manager(dealership_id)).json()
    assert body == {"channel": "website", "ready": True, "missing": []}
    assert publishing_profile_ready(db_session, dealership_id=uuid.UUID(dealership_id)) is True
    assert publishing_profile_missing_items(db_session, dealership_id=uuid.UUID(dealership_id)) == []


def test_an_inactive_location_needs_no_lead_email(client, db_session):
    dealership = _create_dealership(client)
    dealership_id = uuid.UUID(dealership["id"])
    db_session.add(Location(tenant_id=dealership_id, name="Alte Filiale", is_active=False, location_types=[]))
    db_session.commit()
    missing = publishing_profile_missing_items(db_session, dealership_id=dealership_id)
    assert sum(1 for m in missing if m.code == "lead_email_missing") == 1  # the seat only

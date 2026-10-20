"""KAN-200 — the Users screen's fields (Dealer Administration, Users →
Fields), the user's sites (`user_location`, FR-A-02), the list's search,
and the signature image in the object store."""

import uuid
from pathlib import Path

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.main import app as fastapi_app
from app.platform.models.dealership import Location
from tests.identity_support import create_dealership, create_user, platform_admin_headers

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _manager(dealership: str, user: dict) -> dict[str, str]:
    tid = uuid.UUID(dealership)
    token = create_access_token(
        user_id=uuid.UUID(user["id"]),
        tenant_id=tid,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tid)),
        roles=frozenset({AccessRole.SALES}),
        is_dealer_manager=True,
    )
    return {"Authorization": f"Bearer {token}"}


def _location(db_session, dealership: str, name: str) -> str:
    row = Location(tenant_id=uuid.UUID(dealership), name=name, address_locality="Zürich")
    db_session.add(row)
    db_session.commit()
    return str(row.id)


@pytest.fixture()
def object_store(tmp_path: Path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    try:
        yield store
    finally:
        fastapi_app.dependency_overrides.pop(get_object_store, None)


# --- fields --------------------------------------------------------------------------


def test_the_users_fields_round_trip_on_create_and_patch(client):
    dealership = create_dealership(client)
    boss = create_user(client, dealership)
    created = create_user(
        client,
        dealership,
        isDealerManager=False,
        directDial="+41441234599",
        employeeNumber="E-0042",
        department="Verkauf",
        lineManagerUserId=boss["id"],
        salesAdvisorCode="ZH-07",
        commissionRelevant=True,
        validFrom="2026-11-01",
        validTo="2027-03-31",
    )
    assert created["directDial"] == "+41441234599"
    assert created["employeeNumber"] == "E-0042"
    assert created["department"] == "Verkauf"
    assert created["lineManagerUserId"] == boss["id"]
    assert created["salesAdvisorCode"] == "ZH-07"
    assert created["commissionRelevant"] is True
    assert created["validFrom"] == "2026-11-01" and created["validTo"] == "2027-03-31"
    assert created["hasSignatureImage"] is False

    patched = client.patch(
        f"/v1/dealerships/{dealership}/users/{created['id']}",
        json={"department": None, "validTo": None, "lineManagerUserId": None, "commissionRelevant": False},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["department"] is None and body["validTo"] is None and body["lineManagerUserId"] is None
    assert body["commissionRelevant"] is False
    assert body["version"] == 2


@pytest.mark.parametrize("who", ["self", "other_dealership", "unknown"])
def test_the_line_manager_is_another_user_of_the_same_dealership(client, who):
    dealership = create_dealership(client)
    user = create_user(client, dealership)
    if who == "self":
        line_manager = user["id"]
    elif who == "other_dealership":
        line_manager = create_user(client, create_dealership(client))["id"]
    else:
        line_manager = str(uuid.uuid4())
    response = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"lineManagerUserId": line_manager},
        headers={**platform_admin_headers(), "If-Match": "1"},
    )
    assert response.status_code == 422, response.text


def test_the_list_searches_by_name_and_address(client):
    dealership = create_dealership(client)
    create_user(client, dealership, firstName="Marco", lastName="Bianchi", email=f"marco-{uuid.uuid4().hex[:4]}@example.ch")
    create_user(client, dealership, firstName="Anna", lastName="Keller", email=f"anna-{uuid.uuid4().hex[:4]}@example.ch")

    by_name = client.get(f"/v1/dealerships/{dealership}/users?q=bian", headers=platform_admin_headers()).json()
    assert [u["lastName"] for u in by_name["items"]] == ["Bianchi"]
    by_mail = client.get(f"/v1/dealerships/{dealership}/users?q=anna-", headers=platform_admin_headers()).json()
    assert [u["firstName"] for u in by_mail["items"]] == ["Anna"]


# --- locations -----------------------------------------------------------------------


def test_locations_are_listed_per_dealership_and_set_as_primary_plus_additional(client, db_session):
    dealership = create_dealership(client)
    other = create_dealership(client)
    nord = _location(db_session, dealership, "Nord")
    sued = _location(db_session, dealership, "Süd")
    foreign = _location(db_session, other, "Fremd")
    manager = create_user(client, dealership)

    listed = client.get(f"/v1/dealerships/{dealership}/locations", headers=_manager(dealership, manager)).json()
    assert [(row["id"], row["name"]) for row in listed["items"]] == [(nord, "Nord"), (sued, "Süd")]
    assert client.get(f"/v1/dealerships/{other}/locations", headers=_manager(dealership, manager)).status_code == 404

    user = create_user(client, dealership, isDealerManager=False, primaryLocationId=nord, additionalLocationIds=[sued])
    assert user["primaryLocationId"] == nord and user["additionalLocationIds"] == [sued]

    swapped = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"primaryLocationId": sued, "additionalLocationIds": [nord]},
        headers={**_manager(dealership, manager), "If-Match": "1"},
    )
    assert swapped.status_code == 200, swapped.text
    assert swapped.json()["primaryLocationId"] == sued and swapped.json()["additionalLocationIds"] == [nord]

    # The primary alone can change; the additional set is kept.
    only_primary = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"primaryLocationId": None},
        headers={**_manager(dealership, manager), "If-Match": "2"},
    )
    assert only_primary.status_code == 200, only_primary.text
    assert only_primary.json()["primaryLocationId"] is None and only_primary.json()["additionalLocationIds"] == [nord]

    refused = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"primaryLocationId": foreign},
        headers={**_manager(dealership, manager), "If-Match": "3"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {"locationIds": [foreign]}
    audit = client.get(f"/v1/dealerships/{dealership}/audit-log", headers=platform_admin_headers()).json()["items"]
    # One row per PATCH that changed the set (the create's sites are in its own row's scope).
    assert sum(1 for row in audit if row["entityId"] == user["id"] and row["action"] == "locations_set") == 2


# --- the signature image -----------------------------------------------------------------


def test_the_signature_image_is_stored_served_and_removed(client, object_store, tmp_path):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    user = create_user(client, dealership, isDealerManager=False)
    headers = _manager(dealership, manager)

    missing = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/signature", headers=headers)
    assert missing.status_code == 404

    put = client.put(
        f"/v1/dealerships/{dealership}/users/{user['id']}/signature",
        content=_PNG,
        headers={**headers, "If-Match": "1", "Content-Type": "image/png"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["hasSignatureImage"] is True and put.json()["version"] == 2
    # The row holds a key under the dealership's own prefix, never the bytes.
    stored = object_store.get(f"dealerships/{dealership}/users/{user['id']}/signature")
    assert stored is not None and stored.data == _PNG and stored.content_type == "image/png"
    assert not any(p.suffix == ".tmp" for p in (tmp_path / "objects").rglob("*"))

    got = client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/signature", headers=headers)
    assert got.status_code == 200 and got.content == _PNG and got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == "private, no-store"

    replaced = client.put(
        f"/v1/dealerships/{dealership}/users/{user['id']}/signature",
        content=_JPEG,
        headers={**headers, "If-Match": "2", "Content-Type": "image/jpeg"},
    )
    assert replaced.status_code == 200, replaced.text
    assert client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/signature", headers=headers).headers[
        "content-type"
    ] == "image/jpeg"

    removed = client.delete(
        f"/v1/dealerships/{dealership}/users/{user['id']}/signature", headers={**headers, "If-Match": "3"}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["hasSignatureImage"] is False
    assert object_store.get(f"dealerships/{dealership}/users/{user['id']}/signature") is None
    assert client.get(f"/v1/dealerships/{dealership}/users/{user['id']}/signature", headers=headers).status_code == 404
    audit = client.get(f"/v1/dealerships/{dealership}/audit-log", headers=platform_admin_headers()).json()["items"]
    actions = [row["action"] for row in audit if row["entityId"] == user["id"]]
    assert actions.count("signature_image_set") == 2 and actions.count("signature_image_removed") == 1


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"GIF89a" + b"\x00" * 32, "image/gif"),
        (_PNG, "image/jpeg"),  # the header lies about the bytes
        (b"", "image/png"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024), "image/png"),  # over 1 MiB
    ],
)
def test_a_signature_that_is_not_a_small_png_or_jpeg_is_refused(client, object_store, content, content_type):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    user = create_user(client, dealership, isDealerManager=False)
    response = client.put(
        f"/v1/dealerships/{dealership}/users/{user['id']}/signature",
        content=content,
        headers={**_manager(dealership, manager), "If-Match": "1", "Content-Type": content_type},
    )
    assert response.status_code == 422, response.text
    assert client.get(f"/v1/dealerships/{dealership}/users/{user['id']}", headers=platform_admin_headers()).json()[
        "hasSignatureImage"
    ] is False


def test_the_local_store_refuses_a_key_that_escapes_its_root(tmp_path):
    from app.core.object_store import InvalidObjectKey

    store = LocalDirectoryObjectStore(tmp_path)
    for key in ("../outside", "a/../../b", "/etc/passwd", "a//b", ""):
        with pytest.raises(InvalidObjectKey):
            store.put(key, b"x", content_type="text/plain")
    store.put("dealerships/d/users/u/signature", b"x", content_type="text/plain")
    assert store.get("dealerships/d/users/u/signature").data == b"x"
    assert store.delete("dealerships/d/users/u/signature") is True
    assert store.delete("dealerships/d/users/u/signature") is False


def test_the_list_sorts_server_side_and_refuses_an_unknown_field(client):
    """UI/UX Core Principles: every column sortable server-side; an unknown
    field is a 422, never a silent ignore (app/core/sorting.py)."""

    dealership = create_dealership(client)
    create_user(client, dealership, firstName="Zoe", lastName="Zimmer")
    create_user(client, dealership, isDealerManager=False, firstName="Adam", lastName="Abt")
    create_user(client, dealership, isDealerManager=False, firstName="Mia", lastName="Meier")
    url = f"/v1/dealerships/{dealership}/users"
    default = client.get(url, headers=platform_admin_headers()).json()
    assert [u["lastName"] for u in default["items"]] == ["Abt", "Meier", "Zimmer"]
    desc = client.get(f"{url}?sort=lastName:desc", headers=platform_admin_headers()).json()
    assert [u["lastName"] for u in desc["items"]] == ["Zimmer", "Meier", "Abt"]
    paged = client.get(f"{url}?sort=firstName:asc&limit=2", headers=platform_admin_headers()).json()
    assert [u["firstName"] for u in paged["items"]] == ["Adam", "Mia"] and paged["nextCursor"]
    rest = client.get(f"{url}?sort=firstName:asc&limit=2&cursor={paged['nextCursor']}", headers=platform_admin_headers())
    assert [u["firstName"] for u in rest.json()["items"]] == ["Zoe"]
    assert client.get(f"{url}?sort=hairColour:asc", headers=platform_admin_headers()).status_code == 422


def test_the_local_object_store_is_refused_in_production(monkeypatch):
    """KAN-402: a directory on the web service's disk is emptied by every
    deploy while the rows keep their keys — production refuses to start
    with it, like the mailer refuses its `logging` sender."""

    from app.core import object_store
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(object_store, "_store", None)
    with pytest.raises(RuntimeError, match="not a production store"):
        object_store.get_object_store()


def test_the_locations_audit_row_records_the_resulting_set_not_the_sent_fields(client, db_session):
    """A PATCH of the additional sites alone keeps the primary: the append-only
    audit row says so, never a null primary the request did not send."""

    dealership = create_dealership(client)
    nord = _location(db_session, dealership, "Zürich Nord")
    sued = _location(db_session, dealership, "Zürich Süd")
    west = _location(db_session, dealership, "Zürich West")
    user = create_user(client, dealership, isDealerManager=False, primaryLocationId=nord, additionalLocationIds=[sued])
    changed = client.patch(
        f"/v1/dealerships/{dealership}/users/{user['id']}",
        json={"additionalLocationIds": [sued, west]},
        headers={**platform_admin_headers(), "If-Match": str(user["version"])},
    )
    assert changed.status_code == 200, changed.text
    audit = client.get(f"/v1/dealerships/{dealership}/audit-log", headers=platform_admin_headers()).json()["items"]
    rows = [row for row in audit if row["entityId"] == user["id"] and row["action"] == "locations_set"]
    latest = max(rows, key=lambda row: row["createdAt"])
    assert latest["after"] == {"primary_location_id": nord, "additional_location_ids": sorted([sued, west])}

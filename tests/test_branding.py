"""KAN-205 — Dealer Administration › Branding (PRD Logo and brand assets).

What is pinned here:
- the upload: SVG or PNG, judged by the bytes; every refusal has its own
  `details.reason` (JPEG, another format, too small, too large in bytes or
  pixels, a damaged PNG, a malformed SVG, an SVG declaring entities);
- the SVG is sanitised: scripts, event handlers, foreignObject, external
  references and style that reaches outside are gone, the drawing stays;
- the file lives in the object store under the Betrieb's own prefix, the row
  holds the key; a replaced file gets a new key and the old one is removed;
  it is served private, no-store, with a CSP that lets nothing run;
- variants fall back to `primary`;
- the contrast check (WCAG AA 4.5:1) on every save: the verdict is returned
  and audited, a failing pair warns and saves (the PRD does not say it
  blocks — KAN-205 puts that to Anto);
- placements, and the renderer honouring `document_header`;
- scoping: another Betrieb's id is a 404, a non-manager's write a 403, any
  role reads; every write takes If-Match on the dealership's version;
- the preview is a PDF from the one renderer;
- scripts/migrate_logo_urls_to_branding_assets.py.
"""

import base64
import importlib.util
import io
import socket
import uuid
from pathlib import Path

import pytest
from PIL import Image

from app.core.auth import AccessRole
from app.core.errors import UnprocessableEntityError
from app.core.object_store import LocalDirectoryObjectStore, get_object_store
from app.main import app as fastapi_app
from app.platform.models.branding_asset import BrandingAsset
from app.platform.models.dealership import Dealership
from app.platform.services import brand_colors, document_render
from app.platform.services.logo_validation import sanitise_svg, validate_logo
from tests.test_dealership import _bearer, _create_dealership, _token

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_logo_urls_to_branding_assets.py"


def _png(width: int, height: int, mode: str = "RGBA") -> bytes:
    buffer = io.BytesIO()
    color = {"RGBA": (12, 34, 56, 0), "RGB": (12, 34, 56), "L": 40, "1": 0}[mode]
    Image.new(mode, (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(width: int = 800, height: int = 300) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 10, 10)).save(buffer, format="JPEG")
    return buffer.getvalue()


_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80">'
    b'<defs><linearGradient id="g"><stop offset="0" stop-color="#0F766E"/></linearGradient></defs>'
    b'<rect width="240" height="80" fill="url(#g)"/><text x="10" y="50">Garage Muster</text></svg>'
)


def _manager(dealership_id: str, user_id: uuid.UUID | None = None) -> dict[str, str]:
    return _bearer(_token(tenant_id=uuid.UUID(dealership_id), user_id=user_id, is_dealer_manager=True))


def _role(dealership_id: str, role: AccessRole) -> dict[str, str]:
    return _bearer(_token(role, tenant_id=uuid.UUID(dealership_id)))


@pytest.fixture()
def object_store(tmp_path: Path):
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    fastapi_app.dependency_overrides[get_object_store] = lambda: store
    try:
        yield store
    finally:
        fastapi_app.dependency_overrides.pop(get_object_store, None)


def _branding(client, dealership_id: str, headers=None) -> dict:
    response = client.get(f"/v1/dealerships/{dealership_id}/branding", headers=headers or _manager(dealership_id))
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client, dealership_id: str, kind: str, data: bytes, *, version: int, content_type: str = "image/png", headers=None):
    return client.put(
        f"/v1/dealerships/{dealership_id}/branding/assets/{kind}",
        content=data,
        headers={**(headers or _manager(dealership_id)), "If-Match": str(version), "Content-Type": content_type},
    )


def _audit(client, dealership_id: str) -> list[dict]:
    log = client.get(f"/v1/dealerships/{dealership_id}/audit-log", headers=_manager(dealership_id))
    assert log.status_code == 200, log.text
    return log.json()["items"]


# --- the read ---------------------------------------------------------------------


def test_a_new_business_reads_the_shipped_defaults(client):
    dealership = _create_dealership(client)
    body = _branding(client, dealership["id"], headers=_role(dealership["id"], AccessRole.SALES))  # any role reads

    assert body["primaryColor"] == "#7C3AED"
    assert body["secondaryColor"] is None
    assert body["placements"] == {
        "documentHeader": True,
        "priceTag": True,
        "listing": True,
        "emailSignature": True,
        "appHeader": False,
    }
    assert [(a["kind"], a["source"]) for a in body["assets"]] == [
        ("primary", "none"),
        ("mono_dark", "none"),
        ("favicon", "none"),
    ]
    assert body["contrast"] == {
        "minimumRatio": 4.5,
        "passes": True,
        "pairs": [
            {"pair": "primary_on_white", "foreground": "#7C3AED", "background": "#FFFFFF", "ratio": 5.7, "passes": True}
        ],
    }
    assert body["version"] == dealership["version"]


# --- upload: what is accepted ----------------------------------------------------


def test_a_png_is_stored_under_the_business_prefix_and_served_privately(client, object_store, db_session):
    dealership = _create_dealership(client)
    did = dealership["id"]
    png = _png(1200, 400)

    put = _upload(client, did, "primary", png, version=dealership["version"])
    assert put.status_code == 200, put.text
    body = put.json()
    primary = body["assets"][0]
    assert primary["source"] == "own"
    assert (primary["mimeType"], primary["width"], primary["height"], primary["byteSize"]) == ("image/png", 1200, 400, len(png))
    assert body["version"] == dealership["version"] + 1

    row = db_session.query(BrandingAsset).filter_by(dealership_id=uuid.UUID(did)).one()
    assert row.object_key == f"dealerships/{did}/branding/primary/{row.id}"
    stored = object_store.get(row.object_key)
    assert stored is not None and stored.data == png and stored.content_type == "image/png"

    got = client.get(f"/v1/dealerships/{did}/branding/assets/primary", headers=_role(did, AccessRole.SALES))
    assert got.status_code == 200 and got.content == png and got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == "private, no-store"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]

    audit = [e for e in _audit(client, did) if e["action"] == "branding_asset_set"]
    assert len(audit) == 1
    assert audit[0]["before"] == {"kind": "primary", "asset": None}
    assert audit[0]["after"]["asset"]["width"] == 1200


def test_a_png_exactly_600_px_on_its_long_edge_is_taken_portrait_too(client, object_store):
    dealership = _create_dealership(client)
    put = _upload(client, dealership["id"], "primary", _png(200, 600), version=dealership["version"])
    assert put.status_code == 200, put.text


def test_the_file_is_judged_by_its_bytes_not_by_the_content_type(client, object_store):
    dealership = _create_dealership(client)
    did = dealership["id"]
    # A PNG labelled as JPEG is a PNG …
    put = _upload(client, did, "primary", _png(800, 300), version=dealership["version"], content_type="image/jpeg")
    assert put.status_code == 200, put.text
    assert put.json()["assets"][0]["mimeType"] == "image/png"
    # … and a JPEG labelled as PNG is still refused as a JPEG.
    refused = _upload(client, did, "primary", _jpeg(), version=put.json()["version"], content_type="image/png")
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "logo_format_jpeg"


def test_an_svg_is_sanitised_before_it_is_stored(client, object_store, db_session):
    dealership = _create_dealership(client)
    did = dealership["id"]
    hostile = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        b'viewBox="0 0 300 100" onload="alert(1)">'
        b"<script>alert(2)</script>"
        b'<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'
        b'<style>@import url("https://evil.example/x.css"); .a { fill: red }</style>'
        b'<style>.b { fill: url(#g) }</style>'
        b'<use href="https://evil.example/sprite.svg#logo"/>'
        b'<use xlink:href="#shape"/>'
        b'<image href="https://evil.example/tracker.png" width="1" height="1"/>'
        b'<a xlink:href="javascript:alert(3)"><path id="shape" d="M0 0L10 10" onclick="alert(4)"/></a>'
        b'<rect width="10" height="10" style="fill: url(https://evil.example/p.svg#x)" fill="#0F766E"/>'
        b'<set attributeName="href" to="javascript:alert(5)"/>'
        b"</svg>"
    )
    put = _upload(client, did, "primary", hostile, version=dealership["version"], content_type="image/svg+xml")
    assert put.status_code == 200, put.text
    assert put.json()["assets"][0]["mimeType"] == "image/svg+xml"
    assert (put.json()["assets"][0]["width"], put.json()["assets"][0]["height"]) == (300, 100)

    row = db_session.query(BrandingAsset).filter_by(dealership_id=uuid.UUID(did)).one()
    clean = object_store.get(row.object_key).data.decode("utf-8")
    for gone in ("onload", "onclick", "<script", "alert", "foreignObject", "evil.example", "@import", "<a ", "<set"):
        assert gone not in clean, gone
    # What draws stays: the path (unwrapped from its link), the local
    # reference, the safe style, the rect's own fill.
    assert 'd="M0 0L10 10"' in clean
    assert 'xlink:href="#shape"' in clean
    assert ".b { fill: url(#g) }" in clean
    assert 'fill="#0F766E"' in clean


def test_the_sanitiser_keeps_an_ordinary_logo_intact():
    clean = sanitise_svg(_SVG).decode("utf-8")
    assert 'fill="url(#g)"' in clean and "Garage Muster" in clean and 'stop-color="#0F766E"' in clean


def test_an_svg_with_a_public_doctype_is_taken_and_loses_the_doctype():
    with_doctype = (
        b'<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">' + _SVG.split(b"\n", 1)[1]
    )
    clean = sanitise_svg(with_doctype)
    assert b"DOCTYPE" not in clean and b"Garage Muster" in clean


def test_an_inline_png_inside_an_svg_is_kept():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<image href="data:image/png;base64,iVBORw0KGgo=" width="10" height="10"/></svg>'
    )
    assert b"data:image/png;base64,iVBORw0KGgo=" in sanitise_svg(svg)


# --- the KAN-205 review's bypasses: each one closed ----------------------------------
#
# A CSS escape spells `url(` or `@import` without those letters (`\75` is
# "u", `\69` is "i"), and text after a child element inside a `style` element
# is that child's tail — both slipped past the first sanitiser, and the
# renderer fetched what they named (SSRF on every render).

_EVIL = b"evil.example"


def _svg_with(body: bytes) -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">' + body + b'<rect width="10" height="10"/></svg>'


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(rb"<style>@font-face{font-family:x;src:\75rl(http://evil.example:8080/f)}</style>", id="escaped-url"),
        pytest.param(rb"<style>@font-face{font-family:x;src:\000075rl(http://evil.example/f)}</style>", id="long-escape"),
        pytest.param(rb'<style>@\69mport "http://evil.example/x.css";</style>', id="escaped-import"),
        pytest.param(rb"<style>@\49MPORT url(http://evil.example/x.css);</style>", id="escaped-import-upper"),
    ],
)
def test_a_css_escape_in_a_style_element_drops_it(body):
    clean = sanitise_svg(_svg_with(body))
    assert _EVIL not in clean and b"\\" not in clean and b"<style" not in clean
    assert b"<rect" in clean  # the drawing stays


@pytest.mark.parametrize(
    "attribute",
    [
        pytest.param(rb'style="fill:\75rl(http://evil.example/p.svg#x)"', id="style-attribute"),
        pytest.param(rb'fill="\75rl(http://evil.example/p.svg#x)"', id="presentation-attribute"),
        pytest.param(rb'style="fill:&#92;75rl(http://evil.example/p.svg#x)"', id="escape-behind-a-char-reference"),
    ],
)
def test_a_css_escape_in_an_attribute_drops_the_attribute(attribute):
    clean = sanitise_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" ' + attribute + b"/></svg>")
    assert _EVIL not in clean and b"\\" not in clean
    assert b'<rect width="10"' in clean


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"<style><g/>@import url(http://evil.example/x.css);</style>", id="tail-after-child"),
        pytest.param(b"<style>.a{fill:red}<g/>@font-face{src:url(http://evil.example/f)}</style>", id="tail-after-safe-css"),
        pytest.param(b"<style><g>@import url(http://evil.example/x.css);</g></style>", id="text-inside-child"),
        pytest.param(b"<style><title/>.a{fill:red}</style>", id="harmless-text-still-dropped"),
    ],
)
def test_a_style_element_with_a_child_element_is_dropped_whole(body):
    clean = sanitise_svg(_svg_with(body))
    assert _EVIL not in clean and b"<style" not in clean and b"@import" not in clean


def test_a_stylesheet_hidden_in_an_upload_never_reaches_the_store(client, object_store, db_session):
    dealership = _create_dealership(client)
    hostile = _svg_with(rb"<style>@font-face{font-family:x;src:\75rl(http://evil.example:8080/f)}</style>")
    put = _upload(client, dealership["id"], "primary", hostile, version=dealership["version"], content_type="image/svg+xml")
    assert put.status_code == 200, put.text
    row = db_session.query(BrandingAsset).filter_by(dealership_id=uuid.UUID(dealership["id"])).one()
    assert _EVIL not in object_store.get(row.object_key).data


# --- the renderer's own wall: it loads data: URIs and nothing else -------------------


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:9/x", "https://example.com/logo.png", "file:///etc/hostname", "ftp://example.com/x", "logo.png"],
)
def test_the_renderer_fetcher_refuses_everything_but_data_uris(url):
    with pytest.raises(ValueError):
        document_render.local_url_fetcher()(url)


def test_the_renderer_fetcher_serves_a_data_uri():
    response = document_render.local_url_fetcher()("data:text/plain;base64,aGk=")
    assert response.read() == b"hi"


def test_a_render_connects_nowhere_whatever_the_html_names():
    """End to end: a document whose HTML names a listening local port — as an
    image, a stylesheet, an @import, a font and inside an inline SVG — renders
    without a single connection reaching that port. (With WeasyPrint's default
    fetcher this test fails: the listener sees the connection.)"""

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.5)
        target = f"http://127.0.0.1:{listener.getsockname()[1]}"
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(' + target + "/svg.css);</style>"
            '<image href="' + target + '/svg.png" width="1" height="1"/></svg>'
        )
        inline_svg = base64.b64encode(svg.encode()).decode()
        page = (
            f'<html><head><link rel="stylesheet" href="{target}/a.css">'
            f"<style>@import url({target}/b.css); @font-face {{ font-family: x; src: url({target}/f.woff) }}"
            f" body {{ font-family: x; background: url({target}/bg.png) }}</style></head>"
            f'<body><img src="{target}/logo.png"><img src="data:image/svg+xml;base64,{inline_svg}">x</body></html>'
        )
        pdf = document_render.write_pdf(page)
        assert pdf.startswith(b"%PDF")
        with pytest.raises(TimeoutError):
            listener.accept()


# --- upload: one test per refusal ------------------------------------------------


_BILLION_LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'
)
_XXE = (
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'
)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        pytest.param(_jpeg(), "logo_format_jpeg", id="jpeg"),
        pytest.param(b"GIF89a" + b"\x00" * 64, "logo_format_unsupported", id="gif"),
        pytest.param(b"%PDF-1.7\n" + b"\x00" * 64, "logo_format_unsupported", id="pdf"),
        pytest.param(b'<html xmlns="http://www.w3.org/1999/xhtml"></html>', "logo_format_unsupported", id="xml-not-svg"),
        pytest.param(_png(599, 200), "logo_too_small", id="png-599px"),
        pytest.param(_png(10_001, 1, mode="L"), "logo_dimensions_too_large", id="png-10001px"),
        pytest.param(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "logo_png_damaged", id="png-damaged"),
        pytest.param(b'<svg xmlns="http://www.w3.org/2000/svg"><g></svg>', "logo_svg_invalid", id="svg-malformed"),
        pytest.param(_BILLION_LAUGHS, "logo_svg_entities", id="svg-billion-laughs"),
        pytest.param(_XXE, "logo_svg_entities", id="svg-xxe"),
    ],
)
def test_each_refused_upload_says_why(client, object_store, content, reason):
    dealership = _create_dealership(client)
    refused = _upload(client, dealership["id"], "primary", content, version=dealership["version"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"]["reason"] == reason
    # Nothing was stored and the version did not move.
    assert _branding(client, dealership["id"])["version"] == dealership["version"]
    root = object_store._root
    assert not root.exists() or not any(root.rglob("*"))


def test_an_empty_file_is_not_a_logo():
    # At the API an empty body is the schema's 422 before this runs.
    with pytest.raises(UnprocessableEntityError) as refused:
        validate_logo(b"")
    assert refused.value.details == {"reason": "logo_format_unsupported"}


def test_too_small_names_the_size_and_the_print_reason(client, object_store):
    dealership = _create_dealership(client)
    refused = _upload(client, dealership["id"], "primary", _png(400, 120), version=dealership["version"])
    error = refused.json()["error"]
    assert error["details"] == {"reason": "logo_too_small", "width": 400, "height": 120, "minLongEdge": 600}
    assert "50 mm" in error["message"] and "600 px" in error["message"]


def test_a_body_over_5_mib_is_refused_before_it_is_decoded(client, object_store):
    # The request body is read; it is never opened as an image.
    dealership = _create_dealership(client)
    refused = _upload(
        client, dealership["id"], "primary", b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024), version=dealership["version"]
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "logo_file_too_large"


def test_a_png_of_100_megapixels_is_refused_though_it_fits_in_5_mib(client, object_store):
    # The review's case: 10'000 × 10'000 px is within the long-edge cap and,
    # one colour, compresses to a few kilobytes.
    big = _png(10_000, 10_000, mode="1")
    assert len(big) < 5 * 1024 * 1024
    dealership = _create_dealership(client)
    refused = _upload(client, dealership["id"], "primary", big, version=dealership["version"])
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["details"] == {
        "reason": "logo_too_many_pixels",
        "width": 10_000,
        "height": 10_000,
        "maxMegapixels": 16,
    }


def test_the_pixel_cap_is_16_megapixels_exactly():
    assert validate_logo(_png(4_000, 4_000, mode="1")).width == 4_000
    with pytest.raises(UnprocessableEntityError) as refused:
        validate_logo(_png(4_000, 4_001, mode="1"))
    assert refused.value.details["reason"] == "logo_too_many_pixels"


# --- variants and replacement ----------------------------------------------------


def test_variants_fall_back_to_the_primary(client, object_store):
    dealership = _create_dealership(client)
    did = dealership["id"]
    assert client.get(f"/v1/dealerships/{did}/branding/assets/favicon", headers=_manager(did)).status_code == 404

    primary = _png(900, 300)
    version = _upload(client, did, "primary", primary, version=dealership["version"]).json()["version"]
    body = _branding(client, did)
    assert [(a["kind"], a["source"]) for a in body["assets"]] == [
        ("primary", "own"),
        ("mono_dark", "primary"),
        ("favicon", "primary"),
    ]
    assert body["assets"][1]["mimeType"] is None  # describes the variant's own file: none
    assert client.get(f"/v1/dealerships/{did}/branding/assets/mono_dark", headers=_manager(did)).content == primary

    dark = _png(900, 300, mode="RGB")
    version = _upload(client, did, "mono_dark", dark, version=version).json()["version"]
    assert client.get(f"/v1/dealerships/{did}/branding/assets/mono_dark", headers=_manager(did)).content == dark
    assert client.get(f"/v1/dealerships/{did}/branding/assets/favicon", headers=_manager(did)).content == primary

    removed = client.delete(f"/v1/dealerships/{did}/branding/assets/mono_dark", headers={**_manager(did), "If-Match": str(version)})
    assert removed.status_code == 200, removed.text
    assert removed.json()["assets"][1]["source"] == "primary"
    assert client.get(f"/v1/dealerships/{did}/branding/assets/mono_dark", headers=_manager(did)).content == primary
    again = client.delete(
        f"/v1/dealerships/{did}/branding/assets/mono_dark", headers={**_manager(did), "If-Match": str(removed.json()["version"])}
    )
    assert again.status_code == 404
    actions = [e["action"] for e in _audit(client, did)]
    assert actions.count("branding_asset_set") == 2 and actions.count("branding_asset_removed") == 1


def test_a_replaced_file_gets_a_new_key_and_the_old_object_goes(client, object_store, db_session):
    dealership = _create_dealership(client)
    did = dealership["id"]
    version = _upload(client, did, "primary", _png(800, 300), version=dealership["version"]).json()["version"]
    first = db_session.query(BrandingAsset).filter_by(dealership_id=uuid.UUID(did)).one().object_key
    db_session.expire_all()

    replaced = _upload(client, did, "primary", _SVG, version=version, content_type="image/svg+xml")
    assert replaced.status_code == 200, replaced.text
    second = db_session.query(BrandingAsset).filter_by(dealership_id=uuid.UUID(did)).one().object_key
    assert second != first
    assert object_store.get(first) is None
    assert object_store.get(second).content_type == "image/svg+xml"


# --- scoping and concurrency -------------------------------------------------------


def test_another_business_is_a_404_and_a_non_manager_write_a_403(client, object_store):
    mine = _create_dealership(client)
    other = _create_dealership(client)
    _upload(client, other["id"], "primary", _png(800, 300), version=other["version"])
    intruder = _manager(mine["id"])
    base = f"/v1/dealerships/{other['id']}/branding"

    assert client.get(base, headers=intruder).status_code == 404
    assert client.get(f"{base}/assets/primary", headers=intruder).status_code == 404
    assert client.get(f"{base}/preview", headers=intruder).status_code == 404
    assert client.patch(base, json={"primaryColor": "#000000"}, headers={**intruder, "If-Match": "2"}).status_code == 404
    assert _upload(client, other["id"], "primary", _png(800, 300), version=2, headers=intruder).status_code == 404
    assert client.delete(f"{base}/assets/primary", headers={**intruder, "If-Match": "2"}).status_code == 404

    sales = _role(mine["id"], AccessRole.SALES)
    mine_base = f"/v1/dealerships/{mine['id']}/branding"
    assert client.patch(mine_base, json={"primaryColor": "#000000"}, headers={**sales, "If-Match": "1"}).status_code == 403
    assert _upload(client, mine["id"], "primary", _png(800, 300), version=1, headers=sales).status_code == 403
    assert client.get(mine_base, headers=sales).status_code == 200


def test_every_write_takes_if_match_on_the_dealerships_version(client, object_store):
    dealership = _create_dealership(client)
    did = dealership["id"]
    base = f"/v1/dealerships/{did}/branding"
    missing = client.patch(base, json={"primaryColor": "#000000"}, headers=_manager(did))
    assert missing.status_code == 400
    stale = client.patch(base, json={"primaryColor": "#000000"}, headers={**_manager(did), "If-Match": "99"})
    assert stale.status_code == 409 and stale.json()["error"]["details"]["currentVersion"] == dealership["version"]
    assert _upload(client, did, "primary", _png(800, 300), version=99).status_code == 409


# --- colours and the contrast check -----------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "ratio"),
    [
        ("#000000", "#FFFFFF", 21.0),
        ("#FFFFFF", "#FFFFFF", 1.0),
        ("#7C3AED", "#FFFFFF", 5.6986),
        ("#767676", "#FFFFFF", 4.5422),
        ("#777777", "#FFFFFF", 4.4781),
        ("#FACC15", "#FFFFFF", 1.5314),
        ("#FACC15", "#1E3A8A", 6.7637),
    ],
)
def test_the_contrast_ratio_is_wcags(a, b, ratio):
    # The same vectors as frontend/apps/dms/src/components/branding/contrast.test.ts.
    assert brand_colors.contrast_ratio(a, b) == pytest.approx(ratio, abs=1e-4)
    assert brand_colors.contrast_ratio(b, a) == pytest.approx(ratio, abs=1e-4)


def test_aa_is_judged_on_the_unrounded_ratio():
    [grey] = brand_colors.judge_brand_colors("#767676", None)
    [lighter] = brand_colors.judge_brand_colors("#777777", None)
    assert grey.passes and not lighter.passes


def test_a_failing_contrast_warns_saves_and_is_audited(client):
    dealership = _create_dealership(client)
    did = dealership["id"]
    saved = client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"primaryColor": "#facc15", "secondaryColor": "#1E3A8A"},
        headers={**_manager(did), "If-Match": str(dealership["version"])},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["primaryColor"], body["secondaryColor"]) == ("#FACC15", "#1E3A8A")  # upper-cased
    assert body["contrast"]["passes"] is False
    assert {p["pair"]: (p["ratio"], p["passes"]) for p in body["contrast"]["pairs"]} == {
        "primary_on_white": (1.53, False),
        "secondary_on_white": (10.36, True),
        "secondary_on_primary": (6.76, True),
    }
    [entry] = [e for e in _audit(client, did) if e["action"] == "branding_updated"]
    assert entry["before"] == {"brand_primary_color": "#7C3AED", "brand_secondary_color": None}
    assert entry["after"]["brand_primary_color"] == "#FACC15"
    assert entry["after"]["contrast"]["primary_on_white"] == {"ratio": 1.53, "passes": False}


def test_colours_must_be_hex_and_the_primary_cannot_be_cleared(client):
    dealership = _create_dealership(client)
    did = dealership["id"]
    headers = {**_manager(did), "If-Match": str(dealership["version"])}
    for body in ({"primaryColor": "yellow"}, {"primaryColor": "#FFF"}, {"secondaryColor": "#12345G"}, {"primaryColor": None}):
        assert client.patch(f"/v1/dealerships/{did}/branding", json=body, headers=headers).status_code == 422, body

    set_ = client.patch(f"/v1/dealerships/{did}/branding", json={"secondaryColor": "#0F766E"}, headers=headers).json()
    cleared = client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"secondaryColor": None},
        headers={**_manager(did), "If-Match": str(set_["version"])},
    ).json()
    assert cleared["secondaryColor"] is None and len(cleared["contrast"]["pairs"]) == 1


def test_an_unchanged_save_moves_no_version(client):
    dealership = _create_dealership(client)
    did = dealership["id"]
    same = client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"primaryColor": "#7C3AED", "placements": {"appHeader": False}},
        headers={**_manager(did), "If-Match": str(dealership["version"])},
    )
    assert same.status_code == 200 and same.json()["version"] == dealership["version"]
    assert not [e for e in _audit(client, did) if e["action"] == "branding_updated"]


# --- placements and the renderer ----------------------------------------------------


def test_placements_save_one_by_one(client):
    dealership = _create_dealership(client)
    did = dealership["id"]
    saved = client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"placements": {"priceTag": False, "appHeader": True}},
        headers={**_manager(did), "If-Match": str(dealership["version"])},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["placements"] == {
        "documentHeader": True,
        "priceTag": False,
        "listing": True,
        "emailSignature": True,
        "appHeader": True,
    }
    assert client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"placements": {"listing": None}},
        headers={**_manager(did), "If-Match": str(saved.json()["version"])},
    ).status_code == 422


def test_the_document_header_prints_the_uploaded_logo_until_its_placement_is_off(client, object_store, db_session):
    dealership = _create_dealership(client)
    did = dealership["id"]
    version = _upload(client, did, "primary", _SVG, version=dealership["version"], content_type="image/svg+xml").json()["version"]
    row = db_session.get(Dealership, uuid.UUID(did))

    logo = document_render.document_header_logo(db_session, row, store=object_store)
    assert logo is not None and logo.content_type == "image/svg+xml"
    html = document_render._render_logo(row, logo)
    assert '<img ' in html and 'src="data:image/svg+xml;base64,' in html

    client.patch(
        f"/v1/dealerships/{did}/branding",
        json={"placements": {"documentHeader": False}},
        headers={**_manager(did), "If-Match": str(version)},
    )
    db_session.expire_all()
    row = db_session.get(Dealership, uuid.UUID(did))
    assert document_render.document_header_logo(db_session, row, store=object_store) is None
    assert "<img" not in document_render._render_logo(row, None)


def test_the_preview_is_a_pdf_from_the_one_renderer(client, object_store):
    dealership = _create_dealership(client)
    did = dealership["id"]
    _upload(client, did, "primary", _png(800, 300), version=dealership["version"])
    preview = client.get(f"/v1/dealerships/{did}/branding/preview", headers=_role(did, AccessRole.SALES))
    assert preview.status_code == 200, preview.text
    assert preview.headers["content-type"] == "application/pdf"
    assert preview.headers["cache-control"] == "private, no-store"
    assert preview.content.startswith(b"%PDF")


# --- scripts/migrate_logo_urls_to_branding_assets.py ---------------------------------


def _script():
    spec = importlib.util.spec_from_file_location("migrate_logo_urls_to_branding_assets", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_logo_url_script_migrates_what_it_can_fetch_and_reports_the_rest(client, db_session, tmp_path):
    script = _script()
    store = LocalDirectoryObjectStore(tmp_path / "objects")
    good, jpeg, gone = (_create_dealership(client) for _ in range(3))
    urls = {
        good["id"]: "https://cdn.example/good.png",
        jpeg["id"]: "https://cdn.example/photo.jpg",
        gone["id"]: "ftp://cdn.example/gone.png",
    }
    for did, url in urls.items():
        db_session.get(Dealership, uuid.UUID(did)).logo_url = url
    db_session.commit()
    bodies = {"https://cdn.example/good.png": _png(900, 300), "https://cdn.example/photo.jpg": _jpeg()}

    def fetcher(url: str) -> bytes:
        if url.startswith("ftp:"):
            return script.fetch(url)  # the real refusal of a non-http(s) URL
        return bodies[url]

    dry = {str(d.id): outcome for d, outcome in script.migrate(db_session, store, commit=False, fetcher=fetcher)}
    assert dry == {good["id"]: "would migrate", jpeg["id"]: "rejected logo_format_jpeg", gone["id"]: "unfetchable not an http(s) URL"}
    assert db_session.query(BrandingAsset).count() == 0

    real = {str(d.id): outcome for d, outcome in script.migrate(db_session, store, commit=True, fetcher=fetcher)}
    assert real[good["id"]] == "migrated"
    db_session.expire_all()
    migrated = db_session.get(Dealership, uuid.UUID(good["id"]))
    assert migrated.logo_url is None and migrated.version == good["version"] + 1
    asset = db_session.query(BrandingAsset).one()
    assert asset.dealership_id == migrated.id and asset.uploaded_by is None
    assert store.get(asset.object_key).data == bodies["https://cdn.example/good.png"]
    assert db_session.get(Dealership, uuid.UUID(jpeg["id"])).logo_url == "https://cdn.example/photo.jpg"

    again = {str(d.id): outcome for d, outcome in script.migrate(db_session, store, commit=True, fetcher=fetcher)}
    assert good["id"] not in again and db_session.query(BrandingAsset).count() == 1

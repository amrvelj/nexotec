"""What a logo upload must be before it is stored (KAN-205; Dealer
Administration PRD, Logo and brand assets).

- **SVG preferred, PNG accepted, JPEG rejected** — no transparency, and it
  prints with a grey box behind it on every invoice. Anything else (GIF,
  WebP, PDF, …) is not a logo format we print.
- **PNG at ≥ 600 px on the long edge** — WeasyPrint lays out at 96 CSS
  px/inch; a logo printed 50 mm wide needs roughly 600 px to survive at
  300 dpi. Refused with that explanation, never "file too small".
- **Judged by the bytes**, never by the Content-Type the client sent: a
  JPEG labelled `image/png` is still a JPEG.
- **SVG is sanitised**, not trusted: it is XML that a browser or a renderer
  would execute. See `sanitise_svg`.

Every refusal is a 422 with a stable `details.reason` the SPA translates.

The SVG sanitiser uses the standard library only (Anto's routine: no new
dependency where a robust approach exists in what we have). Two layers:

1. Expat reads the document once with handlers that refuse every entity
   declaration (internal or external, general or parameter) and never load
   an external DTD — the XML-bomb and XXE vectors close before any tree is
   built. A DOCTYPE without an internal subset (Illustrator writes
   `<!DOCTYPE svg PUBLIC …>`) is fine: it is never fetched and is dropped.
2. The tree is rebuilt from an **allowlist** of SVG elements. Everything
   else is dropped with its subtree (`script`, `foreignObject`, animation
   elements that can rewrite an attribute, `iframe`, editor metadata in
   foreign namespaces); `a` is unwrapped (its children kept, the link gone).
   On the kept elements: no event handler (`on*`), no attribute in a foreign
   namespace but `xml:space`/`xml:lang`, `href`/`xlink:href` only to a
   fragment of the same document (`#id`) — `image` may also carry an inline
   PNG or JPEG (`data:image/png;base64,…`) — and no attribute or `style` element
   that reaches outside the document (`url(` other than `url(#…)`,
   `@import`, `image-set(`, `javascript:`, `expression(`). Comments and processing
   instructions are not carried over.
"""

import dataclasses
import io
import re
import struct
import xml.etree.ElementTree as ET
from xml.parsers import expat

from PIL import Image

from app.core.errors import UnprocessableEntityError

MIN_PNG_LONG_EDGE_PX = 600
MAX_LONG_EDGE_PX = 10_000
MAX_BYTES = 5 * 1024 * 1024

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
# Serialised as a plain `<svg xmlns="…">` with `xlink:href`, not with
# ElementTree's generated prefixes (its `default_namespace` option refuses
# the unprefixed attributes every SVG has). Nothing else in the application
# serialises these namespaces through ElementTree.
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


@dataclasses.dataclass(frozen=True)
class ValidatedLogo:
    data: bytes
    mime_type: str  # image/png · image/svg+xml
    width: int | None
    height: int | None


def _refuse(message: str, reason: str, **details: object) -> UnprocessableEntityError:
    return UnprocessableEntityError(message, details={"reason": reason, **details})


def validate_logo(data: bytes) -> ValidatedLogo:
    if not data:
        raise _refuse("The request body must be the logo file itself.", "logo_format_unsupported")
    if len(data) > MAX_BYTES:
        raise _refuse(
            "The logo file is too large — at most 5 MiB.",
            "logo_file_too_large",
            maxBytes=MAX_BYTES,
            bytes=len(data),
        )
    if data.startswith(_JPEG):
        raise _refuse(
            "JPEG is not accepted for a logo: it has no transparency, so it prints with a grey box behind it "
            "on every document. Upload an SVG (preferred) or a PNG.",
            "logo_format_jpeg",
        )
    if data.startswith(_PNG):
        return _validate_png(data)
    if _looks_like_xml(data):
        return _validate_svg(data)
    raise _refuse("A logo must be an SVG (preferred) or a PNG file.", "logo_format_unsupported")


# --- PNG ---------------------------------------------------------------------


def _validate_png(data: bytes) -> ValidatedLogo:
    # Pillow is the decoder WeasyPrint itself uses for raster images, so a
    # PNG it opens and verifies is one the renderer can print. verify()
    # checks the chunk structure and CRCs without decoding the pixels.
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            width, height = image.size
            image.verify()
    except Image.DecompressionBombError:
        raise _too_many_pixels() from None
    except (OSError, SyntaxError, ValueError, EOFError, struct.error):  # what Pillow raises on a broken file
        raise _refuse("The PNG file is damaged and cannot be read.", "logo_png_damaged") from None
    if fmt != "PNG":
        raise _refuse("A logo must be an SVG (preferred) or a PNG file.", "logo_format_unsupported")
    long_edge = max(width, height)
    if long_edge > MAX_LONG_EDGE_PX:
        raise _too_many_pixels()
    if long_edge < MIN_PNG_LONG_EDGE_PX:
        raise _refuse(
            f"This PNG is {width} × {height} px. A logo printed 50 mm wide needs roughly "
            f"{MIN_PNG_LONG_EDGE_PX} px on its long edge to stay sharp at 300 dpi — upload a larger PNG, "
            "or an SVG, which is sharp at any size.",
            "logo_too_small",
            width=width,
            height=height,
            minLongEdge=MIN_PNG_LONG_EDGE_PX,
        )
    return ValidatedLogo(data=data, mime_type="image/png", width=width, height=height)


def _too_many_pixels() -> UnprocessableEntityError:
    return _refuse(
        f"The image is larger than {MAX_LONG_EDGE_PX} px on its long edge — more than any document needs.",
        "logo_dimensions_too_large",
        maxLongEdge=MAX_LONG_EDGE_PX,
    )


# --- SVG ---------------------------------------------------------------------


def _looks_like_xml(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith(b"<")


def _svg_invalid() -> UnprocessableEntityError:
    return _refuse("The SVG file is not a well-formed SVG document.", "logo_svg_invalid")


def _refuse_entities(data: bytes) -> None:
    """Layer 1: one Expat pass that refuses any entity declaration and never
    reads an external DTD or entity."""

    def entity_declared(*_args: object) -> None:
        raise _refuse(
            "The SVG declares XML entities, which can be used to attack the systems that read it. "
            "Export the logo again without a DTD.",
            "logo_svg_entities",
        )

    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.EntityDeclHandler = entity_declared
    parser.UnparsedEntityDeclHandler = entity_declared
    parser.ExternalEntityRefHandler = lambda *_args: 0  # never resolved (0 = refuse)
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise _svg_invalid() from None


_ALLOWED_ELEMENTS = frozenset(
    {
        "svg", "g", "defs", "symbol", "use", "title", "desc", "switch",
        "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
        "text", "tspan", "textPath",
        "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask", "marker",
        "image", "style",
        "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite", "feConvolveMatrix",
        "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow", "feFlood",
        "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feMerge", "feMergeNode",
        "feMorphology", "feOffset", "fePointLight", "feSpecularLighting", "feSpotLight", "feTile",
        "feTurbulence",
    }
)  # fmt: skip
_UNWRAPPED_ELEMENTS = frozenset({"a"})
_ALLOWED_XML_ATTRIBUTES = frozenset({f"{{{XML_NS}}}space", f"{{{XML_NS}}}lang"})
_HREF_ATTRIBUTES = frozenset({"href", f"{{{XLINK_NS}}}href"})
_ATTRIBUTE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_URL_CALL = re.compile(r"url\s*\(\s*(['\"]?)\s*([^'\")\s]*)", re.IGNORECASE)
_DANGEROUS_CSS = re.compile(r"@import|javascript\s*:|expression\s*\(|image-set\s*\(|-moz-binding|behavior\s*:", re.IGNORECASE)
_INLINE_RASTER = re.compile(r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/=\s]+$")


def _local_name(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns, name
    return None, tag


def _reaches_outside(value: str) -> bool:
    """True when a CSS or attribute value would load something from outside
    the document or run code."""

    if _DANGEROUS_CSS.search(value):
        return True
    return any(not target.startswith("#") for _quote, target in _URL_CALL.findall(value))


def _safe_href(element_name: str, value: str) -> bool:
    value = value.strip()
    if value.startswith("#"):
        return True
    return element_name == "image" and bool(_INLINE_RASTER.match(value))


def _copy_attributes(source: ET.Element, target: ET.Element, element_name: str) -> None:
    for key, value in source.attrib.items():
        ns, name = _local_name(key)
        if key in _HREF_ATTRIBUTES:
            if _safe_href(element_name, value):
                target.set(key, value)
            continue
        if ns is not None:
            if key in _ALLOWED_XML_ATTRIBUTES:
                target.set(key, value)
            continue
        if not _ATTRIBUTE_NAME.match(name) or name.lower().startswith("on"):
            continue
        if _reaches_outside(value):
            continue
        target.set(key, value)


def _sanitised_children(source: ET.Element, target: ET.Element) -> None:
    for child in source:
        if not isinstance(child.tag, str):  # a comment or processing instruction
            continue
        ns, name = _local_name(child.tag)
        if ns != SVG_NS:
            continue
        if name in _UNWRAPPED_ELEMENTS:
            # The link goes, what it wrapped stays — in place of the <a>.
            holder = ET.Element(f"{{{SVG_NS}}}g")
            _sanitised_children(child, holder)
            for grandchild in holder:
                target.append(grandchild)
            continue
        if name not in _ALLOWED_ELEMENTS:
            continue
        if name == "image" and not any(
            _safe_href("image", child.get(attr, "")) for attr in _HREF_ATTRIBUTES if child.get(attr)
        ):
            continue  # an image of anything but an inline PNG/JPEG would be fetched
        copy = ET.SubElement(target, child.tag)
        _copy_attributes(child, copy, name)
        if name == "style":
            css = child.text or ""
            if _reaches_outside(css):
                target.remove(copy)
                continue
            copy.text = css
        else:
            copy.text = child.text
        _sanitised_children(child, copy)
        copy.tail = child.tail


def _length_px(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*(px)?\s*", value)
    return float(match.group(1)) if match else None


def _intrinsic_size(root: ET.Element) -> tuple[int | None, int | None]:
    width, height = _length_px(root.get("width")), _length_px(root.get("height"))
    if width is None or height is None:
        view_box = (root.get("viewBox") or "").replace(",", " ").split()
        if len(view_box) == 4:
            try:
                width, height = float(view_box[2]), float(view_box[3])
            except ValueError:
                width = height = None
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None
    return round(width), round(height)


def sanitise_svg(data: bytes) -> bytes:
    """The SVG rebuilt from the allowlist above, as UTF-8 bytes. Raises the
    422 refusals for a document that is not SVG, not well-formed, or
    declares entities."""

    _refuse_entities(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise _svg_invalid() from None
    if root.tag != f"{{{SVG_NS}}}svg":
        raise _refuse("A logo must be an SVG (preferred) or a PNG file.", "logo_format_unsupported")
    clean = ET.Element(root.tag)
    _copy_attributes(root, clean, "svg")
    clean.text = root.text
    _sanitised_children(root, clean)
    return ET.tostring(clean, encoding="utf-8", xml_declaration=True)


def _validate_svg(data: bytes) -> ValidatedLogo:
    clean = sanitise_svg(data)
    width, height = _intrinsic_size(ET.fromstring(clean))
    return ValidatedLogo(data=clean, mime_type="image/svg+xml", width=width, height=height)

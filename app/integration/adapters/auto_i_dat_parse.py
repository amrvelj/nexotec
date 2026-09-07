"""Parsing helpers for auto-i-dat's ``Suchen`` responses (Configurator
C-0 / KAN-38, PR 1).

Every ``Suchen`` call returns a single AES-encrypted XML string
(*Webservice Fahrzeuge* p4). Once decrypted the document is always the
same shape::

    <Autoi{Datenname} ...>
      <Info>
        <Status>0</Status>
        <StatusMsg>OK</StatusMsg>
        <TotalSeiten>4</TotalSeiten>   <!-- only when Einstellungen=Seite;ProSeite -->
        <Anzahl>174</Anzahl>           <!-- only when Einstellungen=NurAnzahl=1 -->
      </Info>
      <{Datenname}> ... </{Datenname}>   <!-- zero or more rows -->
      ...
    </Autoi{Datenname}>

``Status``: ``0`` OK, ``1`` webservice in maintenance, ``2`` no data
found (p4). ``2`` is *not* an error — it is an empty result.

This module is deliberately free of ``zeep``, the SOAP transport and any
network call, so it is unit-testable against literal XML snippets exactly
the way ``aes_decrypt.py`` is testable against a self-made vector. The
fourteen new Datennamen (PR 2) reuse these same helpers.

**Root-element prefix**: the spec's own samples are inconsistent
(``<AutoiFahrzeuge>`` on p9 vs ``<AutoifFzKeyChanged>`` on p30). The row
elements are always named exactly after the Datenname, so parsing keys
off the row name and never off the root — the root prefix is only
sanity-checked, never required to match.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


class AutoIDatResponseError(Exception):
    """Base for a structurally-wrong or provider-rejected ``Suchen``
    response. Distinct from a transport failure (``ConnectionError`` /
    ``TimeoutError`` from ``call_with_retry``) and from the gateway's own
    ``ProviderGatewayError`` — this is "the call reached the provider and
    came back unusable".
    """


class ProviderRejectedError(AutoIDatResponseError):
    """The provider returned an empty string — per *Webservice Fahrzeuge*
    p4 that means an invalid ``Benutzername`` / ``Passwort`` / ``Sprache``
    / ``Datenname``. Never a transient condition; a retry would not help.
    """


class ProviderMaintenanceError(AutoIDatResponseError):
    """``<Status>1</Status>`` — "Webservice ist offline (Wartung)". The
    caller may reasonably treat this as transient (retry tomorrow), but it
    is not a per-call retry candidate, so it is raised rather than folded
    into an empty result.
    """


class SuchenResult:
    """The parsed shape of one ``Suchen`` response.

    ``rows`` are the repeating ``<{Datenname}>`` elements (possibly
    empty). ``total_pages`` / ``count`` are populated only when the
    corresponding ``Einstellungen`` were sent.
    """

    __slots__ = ("count", "datenname", "rows", "status", "status_msg", "total_pages")

    def __init__(
        self,
        *,
        datenname: str,
        status: int,
        status_msg: str,
        rows: list[ET.Element],
        total_pages: int | None = None,
        count: int | None = None,
    ) -> None:
        self.datenname = datenname
        self.status = status
        self.status_msg = status_msg
        self.rows = rows
        self.total_pages = total_pages
        self.count = count

    @property
    def first(self) -> ET.Element:
        """The single row for a one-row Datenname (``System``,
        ``KontrollschildInfo`` when unambiguous, …). Raises if the
        provider returned no data — callers that tolerate "no data" check
        ``rows`` themselves.
        """

        if not self.rows:
            raise AutoIDatResponseError(
                f"{self.datenname}: expected at least one row, got none (status {self.status})"
            )
        return self.rows[0]


def parse_suchen_result(datenname: str, xml_text: str | bytes) -> SuchenResult:
    """Parse a decrypted ``Suchen`` XML document.

    Raises ``ProviderRejectedError`` for an empty document,
    ``ProviderMaintenanceError`` for ``<Status>1</Status>``, and
    ``AutoIDatResponseError`` for anything unparseable. ``<Status>2</Status>``
    ("keine Daten") returns normally with ``rows == []``.
    """

    if xml_text is None or (isinstance(xml_text, (str, bytes)) and not str(xml_text).strip()):
        raise ProviderRejectedError(
            f"{datenname}: provider returned an empty response "
            "(invalid Benutzername/Passwort/Sprache/Datenname — Webservice Fahrzeuge p4)"
        )

    try:
        root = ET.fromstring(xml_text)  # trusted provider payload, already AES-decrypted
    except ET.ParseError as exc:
        raise AutoIDatResponseError(f"{datenname}: response is not well-formed XML: {exc}") from exc

    info = root.find("Info")
    status = _opt_int(row_text(info, "Status")) if info is not None else None
    status_msg = (row_text(info, "StatusMsg") or "") if info is not None else ""
    if status is None:
        raise AutoIDatResponseError(f"{datenname}: response carries no <Info><Status>")
    if status == 1:
        raise ProviderMaintenanceError(f"{datenname}: {status_msg or 'webservice in maintenance'}")

    rows = [child for child in root if _local_name(child.tag) == datenname]
    total_pages = _opt_int(row_text(info, "TotalSeiten")) if info is not None else None
    count = _opt_int(row_text(info, "Anzahl")) if info is not None else None
    return SuchenResult(
        datenname=datenname,
        status=status,
        status_msg=status_msg,
        rows=rows,
        total_pages=total_pages,
        count=count,
    )


# --- field readers ---------------------------------------------------------
#
# All tolerant of a missing element and of the provider's "0 means unknown"
# convention where the caller asks for it (kept a caller decision, not baked
# in here — some 0s are real, e.g. a genuinely 0 CHF surcharge).


def row_text(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    found = el.find(tag)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def row_int(el: ET.Element | None, tag: str) -> int | None:
    return _opt_int(row_text(el, tag))


def row_decimal(el: ET.Element | None, tag: str) -> Decimal | None:
    raw = row_text(el, tag)
    if raw is None:
        return None
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def date_ddmmyyyy(raw: str | None) -> dt.date | None:
    """auto-i-dat dates are ``TT.MM.JJJJ`` (``System.UpdateDatum``,
    ``KontrollschildInfo.ErstIVDatum``). ``None`` / blank / ``00.00.0000``
    → ``None``.
    """

    if not raw or raw.strip() in {"", "00.00.0000"}:
        return None
    try:
        # A provider date-only string ("TT.MM.JJJJ"); no time zone applies.
        return dt.datetime.strptime(raw.strip(), "%d.%m.%Y").date()  # noqa: DTZ007
    except ValueError:
        return None


def yyyymm_to_year(raw: str | None) -> int | None:
    """``ProdVon`` / ``ProdBis`` are ``JJJJMM`` strings; ``000000`` means
    "still in production" → ``None``. Returns just the year.
    """

    if not raw:
        return None
    digits = raw.strip()
    if not digits.isdigit() or set(digits) == {"0"}:
        return None
    year = int(digits[:4])
    return year if year > 1900 else None


def csv_list(raw: str | None) -> list[str]:
    """``FzKeyChanged.FzKeyList`` is one comma-joined string, not repeated
    elements (p30).
    """

    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def lang_texts(el: ET.Element | None, base: str) -> dict[str, str]:
    """Language-dependent fields are ``<{base}De>`` / ``<{base}Fr>`` /
    ``<{base}It>`` / ``<{base}En>`` (p4). Returns ``{"de": …}`` for every
    language actually present. PR 1's seven wrappers take ``["de"]`` from
    this; multi-language storage is C-E (ADR-044 — provider text is stored
    as delivered, never translated).
    """

    out: dict[str, str] = {}
    for lang, suffix in (("de", "De"), ("fr", "Fr"), ("it", "It"), ("en", "En")):
        value = row_text(el, f"{base}{suffix}")
        if value is not None:
            out[lang] = value
    return out


def first_lang_text(el: ET.Element | None, base: str, *, prefer: Iterable[str] = ("de", "fr", "it", "en")) -> str | None:
    texts = lang_texts(el, base)
    for lang in prefer:
        if lang in texts:
            return texts[lang]
    return next(iter(texts.values()), None)


# --- internals ------------------------------------------------------------


def _opt_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    """Strip any ``{namespace}`` prefix ElementTree may have folded in."""

    return tag.rsplit("}", 1)[-1]

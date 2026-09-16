"""AutoScout24Adapter (KAN-27, WP-7, ADR-062) — AS24i v34.0, the ONLY one
of the three marketplace channels with a specification on file
("SS 03 AutoScout24 Schnittstellenbeschrieb Version 34.pdf", Emil Frey
Info / API Spezifikationen). Carmarket and Autolina have no such document
anywhere in this repo or in Drive — exactly the situation KAN-36 already
established a precedent for (VIN decode: "no specification exists...
a procurement step, not an engineering one"). Rather than invent a wire
protocol for them, `services.connections.get_enabled_connection` simply
never resolves a connection for either provider_code (no
`IntegrationProvider` row is seeded for them either) — the transmission
service's own "no connection configured" path, already required for a
dealer who hasn't connected AS24 yet, is what surfaces this honestly.

**The transport is FTP, not a request/response call** (§4.4): a single
text file, delivered whole, replaces everything. **Full-delivery
semantics are confirmed verbatim** (§4.2): "Sie liefern in Ihrer Datei
jeweils sämtliche Objekte, welche publiziert werden sollen... Objekte,
welche in Ihrer Datei nicht mehr vorhanden sind, werden aus der
AutoScout24-Datenbank gelöscht" — an object missing from the file is
DELETED, statistics and URL included. This is why
`marketplace_transmission.py` never sends a partial set: doing so would
not under-publish, it would mass-unpublish everything left out.

**The FTP connection is opened lazily, inside `transmit_feed`, never at
adapter construction.** `validate_listing` does no I/O at all — it only
needs `self._connection.config`, already in memory. Constructing the
adapter eagerly (via `app.integration.public.resolve_adapter`, which the
transmission service uses purely to validate) must never open a socket
or resolve a credential a validation-only caller has no other reason to
need.

**v1 scope — cars only, no image relay.** The full "Generelle
Pflichtfelder" mandatory-field set (§4.8.1) is mapped, INCLUDING
`Ausstattung` — which the spec's own table places under that same
mandatory heading (not, as an earlier draft of this file wrongly
believed, under the separate optional "§4.8.2 Zusatzfelder" heading,
which covers a different field, `ProviderAusstattung`). Real equipment
codes need the `equipment_feature_codes` machinery KAN-43 built for the
Configurator, cross-referenced against Anhang 5.2's ~70 bitwise codes —
a real mapping is its own follow-up. Sending `Ausstattung` empty is a
**known, disclosed gap, not a silent one**: §4.3 rule 4 requires every
mandatory field to carry a value, so a real AS24i account may reject
every row on this field alone until that follow-up lands; nothing in
this codebase can detect that rejection today, since AS24i reports
unreadable records only by a later, separate email (§3.4/§4.4), never in
the FTP transfer's own response.
`Aufbau` is body-style free text a dealer types (`StockItem.body_style`)
against what the spec (p.8) implies is a closed, AS24-controlled
vocabulary ("aktuelle Werte sind unter .../auto/suche ersichtlich") — the
same class of imprecision as the `Marke`/`Typ` split below, not
validated against AS24's own list here.
`VehicleType` is hardcoded to `10` (Personenwagen) — `StockItem` carries
no vehicle-kind classification broad enough to distinguish a car from a
Wohnmobil/Lastwagen/Anhänger yet. AS24i also wants the actual image
FILES delivered over the same FTP connection (§4.5, into a `pictures`
subdirectory) — `StockItemMedia.url` is an external reference only, "no
upload/hosting mechanism" (its own docstring), so relaying real image
bytes would mean fetching each URL and re-uploading it, a genuinely
separate feature. Deferred; the vehicle-facts feed transmits without
images for now.
"""

import io
import uuid
from collections.abc import Callable
from typing import Protocol, cast

from sqlalchemy.orm import Session

from app.integration.adapters.base import ProviderAdapter
from app.integration.adapters.marketplace_base import (
    MarketplaceListing,
    MarketplaceListingError,
    MarketplaceTransmissionError,
    TransmissionResult,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend

_VEHICLE_TYPE_CAR = "10"  # Personenwagen — the only StockItem shape this codebase has today.

_FAHRZEUGART_BY_CONDITION = {
    "new": "Neu",
    "used": "Occasion",
    "demo": "Vorführmodell",
    "tagesz": "Tageszulassung",
}

# Field order, once configured, "sollte jedoch nach der Konfiguration
# nicht mehr ändern" (§4.3 point 6) — fixed here rather than computed, so
# a future field addition is an explicit, reviewed change to this tuple.
_FIELD_ORDER = (
    "Kundennummer",
    "FahrzeugNr",
    "VehicleType",
    "Marke",
    "Typ",
    "Aufbau",
    "Aussenfarbe",
    "Fahrzeugart",
    "InvSetzJahr",
    "Kilometer",
    "Preis",
    "Ausstattung",
)


def _sanitise(value: str) -> str:
    # Rule 3 (§4.3): the delimiter must never appear inside a field's own
    # data. For this file format that means both the field separator (;)
    # AND the record separator (CRLF, since build_feed_file joins rows on
    # "\r\n") — an embedded newline in free text would otherwise split
    # one logical row into two physical lines and misalign every field
    # after it. Free-text facts (make/model/body style) are dealer- or
    # provider-sourced strings that could theoretically carry either.
    return value.replace(";", ",").replace("\r", " ").replace("\n", " ")


def _field_row(listing: MarketplaceListing, *, kundennummer: str) -> dict[str, str]:
    if not kundennummer:
        raise MarketplaceListingError(
            stock_item_id=listing.stock_item_id, field="Kundennummer",
            message="Connection has no config.kundennummer set.",
        )
    if not listing.body_style:
        raise MarketplaceListingError(stock_item_id=listing.stock_item_id, field="Aufbau", message="Aufbau is required.")
    if not listing.exterior_colour:
        raise MarketplaceListingError(
            stock_item_id=listing.stock_item_id, field="Aussenfarbe", message="Aussenfarbe is required."
        )
    if listing.odometer_km is None:
        raise MarketplaceListingError(
            stock_item_id=listing.stock_item_id, field="Kilometer", message="Kilometer is required."
        )

    fahrzeugart = _FAHRZEUGART_BY_CONDITION.get(listing.condition)
    if fahrzeugart is None:
        raise MarketplaceListingError(
            stock_item_id=listing.stock_item_id, field="Fahrzeugart",
            message=f"No AS24i Fahrzeugart mapping for condition '{listing.condition}'.",
        )

    if listing.first_registration_date is not None:
        inv_setz_jahr = listing.first_registration_date.strftime("%d.%m.%Y")
    elif listing.condition == "new" and listing.model_year is not None:
        # §4.8.1: "Bei Neuwagen muss das Baujahr geliefert werden" — a new
        # car may genuinely have no first-registration date yet, and the
        # spec accepts the real model year instead. Only the REAL model
        # year is accepted here — no fabricated "today's year" stand-in:
        # a made-up value would be silently wrong data sent to a public
        # listing, which is worse than refusing to publish it at all.
        inv_setz_jahr = str(listing.model_year)
    else:
        raise MarketplaceListingError(
            stock_item_id=listing.stock_item_id, field="InvSetzJahr",
            message="InvSetzJahr is required — no first_registration_date and no model_year available.",
        )

    return {
        "Kundennummer": kundennummer,
        "FahrzeugNr": listing.stock_item_id,
        "VehicleType": _VEHICLE_TYPE_CAR,
        "Marke": _sanitise(listing.make),
        "Typ": _sanitise(listing.model),
        "Aufbau": _sanitise(listing.body_style),
        "Aussenfarbe": _sanitise(listing.exterior_colour),
        "Fahrzeugart": fahrzeugart,
        "InvSetzJahr": inv_setz_jahr,
        "Kilometer": str(listing.odometer_km),
        "Preis": str(listing.price),
        # Mandatory per spec (see module docstring) — sent empty pending
        # the equipment-code mapping follow-up, a disclosed gap.
        "Ausstattung": "",
    }


def build_feed_file(listings: list[MarketplaceListing], *, kundennummer: str) -> bytes:
    """The complete `;`-delimited text file (§4.3): one header line of
    short field names, one line per listing, every field `;`-terminated
    even when empty (rule 4 — none are, here, since every field in
    `_FIELD_ORDER` is mandatory or defaulted to `""`)."""

    rows = [_field_row(listing, kundennummer=kundennummer) for listing in listings]
    lines = [";".join(_FIELD_ORDER) + ";"]
    for row in rows:
        lines.append(";".join(row[name] for name in _FIELD_ORDER) + ";")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


class FtpClient(Protocol):
    """The subset of `ftplib.FTP` this adapter actually uses — a
    structural Protocol, not `ftplib.FTP` itself, so tests supply a
    hand-written fake with no real socket, matching `SoapClient`'s own
    precedent in auto_i_dat_soap.py."""

    def storbinary(self, cmd: str, fp) -> str: ...
    def quit(self) -> str: ...


def build_real_ftp_client(*, host: str, username: str, password: str) -> FtpClient:
    import ftplib

    client = ftplib.FTP(host, timeout=30)
    client.login(user=username, passwd=password)
    return client


class AutoScout24Adapter:
    def __init__(
        self,
        *,
        db: Session,
        connection: IntegrationConnection,
        ftp_client_factory: Callable[[], FtpClient],
        actor_id: uuid.UUID | None,
        purpose: str,
    ) -> None:
        self._db = db
        self._connection = connection
        # A FACTORY, not a constructed client: opening the real FTP
        # connection (and resolving its password) only happens inside
        # transmit_feed, when there is actually something to send.
        # validate_listing never calls this — resolving this adapter
        # purely to validate (app.integration.public.resolve_adapter,
        # which does no logging/circuit-breaker work) must never open a
        # socket or touch a secret it doesn't end up using.
        self._ftp_client_factory = ftp_client_factory
        self._actor_id = actor_id
        self._purpose = purpose

    def validate_listing(self, listing: MarketplaceListing) -> None:
        _field_row(listing, kundennummer=self._connection.config.get("kundennummer", ""))

    def transmit_feed(self, listings: list[MarketplaceListing]) -> TransmissionResult:
        kundennummer = self._connection.config.get("kundennummer", "")
        payload = build_feed_file(listings, kundennummer=kundennummer)
        filename = self._connection.config.get("feedFilename", "autoscout24.txt")
        # A STABLE filename, not one per run (§4.3 point 5 allows any
        # extension/name, but full-delivery only makes sense against one
        # file AS24i keeps re-importing — a new name every run would leave
        # AS24i importing whichever file it last saw, never "replace with
        # this," which is the entire ADR-062 safety property this module
        # exists to preserve).
        try:
            ftp = self._ftp_client_factory()
            try:
                ftp.storbinary(f"STOR {filename}", io.BytesIO(payload))
            finally:
                ftp.quit()
        except MarketplaceTransmissionError:
            raise
        except Exception as exc:
            # Any real transport/auth failure (ftplib's own error types,
            # a timeout, a plain connection error, ...) must surface as
            # MarketplaceTransmissionError — the ONE exception type
            # marketplace_transmission.py's own except clause recognises
            # as "delivery failed, mark everything FAILED and stop."
            # Letting the raw exception through instead would escape
            # unrecognised and propagate past that handler entirely.
            raise MarketplaceTransmissionError(f"AutoScout24 FTP transmission failed: {exc}") from exc
        return TransmissionResult(transmitted_count=len(listings))


def _build_real_adapter(
    db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> "AutoScout24Adapter":
    def ftp_client_factory() -> FtpClient:
        host = connection.config.get("ftpHost")
        username = connection.config.get("ftpUsername")
        if not host or not username:
            raise ValueError(f"Connection {connection.id} has no config.ftpHost/config.ftpUsername set.")
        password = secrets_backend.resolve_secret(connection_id=connection.id, slot=SecretSlot.PASSWORD.value)
        return build_real_ftp_client(host=host, username=username, password=password)

    return AutoScout24Adapter(
        db=db, connection=connection, ftp_client_factory=ftp_client_factory, actor_id=actor_id, purpose=purpose
    )


def _register() -> None:
    # Imported lazily, same reasoning as auto_i_dat_soap.py's own
    # _register(): nothing in adapters/ should import gateway.py at
    # module scope.
    from app.integration.services.gateway import register_adapter_factory

    # The registry's own factory type is written against ProviderAdapter
    # (vehicle-data adapters) — it is genuinely polymorphic at runtime
    # (services/marketplace_transmission.py casts the other direction,
    # back to MarketplaceAdapter, when consuming it), so this is the one
    # place that cast is unavoidable rather than a sign of a real type
    # error.
    factory = cast(Callable[[Session, IntegrationConnection, uuid.UUID | None, str], ProviderAdapter], _build_real_adapter)
    register_adapter_factory("autoscout24", factory)


_register()

"""KAN-27 (WP-7, ADR-062): the AutoScout24 (AS24i v34.0) adapter — the one
marketplace channel with a specification on file ("SS 03 AutoScout24
Schnittstellenbeschrieb Version 34.pdf"). Field values below are lifted
directly from that document's own §4.8.1 mandatory-field table and §4.2's
full-delivery wording.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.integration.adapters.autoscout24 import (
    AutoScout24Adapter,
    _field_row,
    build_feed_file,
)
from app.integration.adapters.marketplace_base import (
    MarketplaceListing,
    MarketplaceListingError,
    MarketplaceTransmissionError,
)


def _listing(**overrides) -> MarketplaceListing:
    defaults = {
        "stock_item_id": "11111111-1111-1111-1111-111111111111",
        "make": "Volkswagen",
        "model": "Golf GTI",
        "body_style": "Kombi",
        "condition": "used",
        "exterior_colour": "Blau metallic",
        "odometer_km": 42000,
        "price": Decimal("19900.00"),
        "first_registration_date": dt.date(2021, 3, 1),
        "model_year": None,
        "image_urls": [],
    }
    defaults.update(overrides)
    return MarketplaceListing(**defaults)


class FakeFtpClient:
    def __init__(self):
        self.stored: list[tuple[str, bytes]] = []
        self.quit_called = False

    def storbinary(self, cmd: str, fp) -> str:
        self.stored.append((cmd, fp.read()))
        return "226 Transfer complete"

    def quit(self) -> str:
        self.quit_called = True
        return "221 Goodbye"


# --- _field_row / build_feed_file (§4.8.1 mandatory fields) ----------------


def test_field_row_maps_every_mandatory_field():
    row = _field_row(_listing(), kundennummer="EFAG-042")
    assert row == {
        "Kundennummer": "EFAG-042",
        "FahrzeugNr": "11111111-1111-1111-1111-111111111111",
        "VehicleType": "10",
        "Marke": "Volkswagen",
        "Typ": "Golf GTI",
        "Aufbau": "Kombi",
        "Aussenfarbe": "Blau metallic",
        "Fahrzeugart": "Occasion",
        "InvSetzJahr": "01.03.2021",
        "Kilometer": "42000",
        "Preis": "19900.00",
        "Ausstattung": "",
    }


@pytest.mark.parametrize(
    "condition,expected",
    [("new", "Neu"), ("used", "Occasion"), ("demo", "Vorführmodell"), ("tagesz", "Tageszulassung")],
)
def test_fahrzeugart_maps_every_stock_item_condition(condition, expected):
    listing = _listing(condition=condition, first_registration_date=dt.date(2020, 1, 1))
    row = _field_row(listing, kundennummer="EFAG-042")
    assert row["Fahrzeugart"] == expected


def test_new_car_with_no_first_registration_uses_the_real_model_year():
    # Schnittstellenbeschrieb v34 p8: "Bei Neuwagen muss das Baujahr
    # geliefert werden" — a new car may have no InvSetzJahr yet, but only
    # a REAL model year is accepted, never a fabricated stand-in.
    listing = _listing(condition="new", first_registration_date=None, model_year=2026)
    row = _field_row(listing, kundennummer="EFAG-042")
    assert row["InvSetzJahr"] == "2026"


def test_new_car_with_no_first_registration_and_no_model_year_is_a_listing_error():
    # No fabricated "today's year" fallback — a made-up InvSetzJahr would
    # be silently wrong data on a public listing, worse than refusing to
    # publish it. In production MarketplaceListing.model_year is always
    # None today (StockItem has no model-year field yet), so a NEW car
    # with no first_registration_date genuinely cannot publish until that
    # follow-up lands — disclosed, not silently guessed around.
    listing = _listing(condition="new", first_registration_date=None, model_year=None)
    with pytest.raises(MarketplaceListingError, match="InvSetzJahr"):
        _field_row(listing, kundennummer="EFAG-042")


def test_used_car_with_no_first_registration_is_a_listing_error():
    listing = _listing(condition="used", first_registration_date=None)
    with pytest.raises(MarketplaceListingError, match="InvSetzJahr"):
        _field_row(listing, kundennummer="EFAG-042")


def test_missing_kundennummer_is_a_listing_error():
    with pytest.raises(MarketplaceListingError, match="Kundennummer"):
        _field_row(_listing(), kundennummer="")


def test_missing_aufbau_is_a_listing_error():
    with pytest.raises(MarketplaceListingError, match="Aufbau"):
        _field_row(_listing(body_style=None), kundennummer="EFAG-042")


def test_missing_aussenfarbe_is_a_listing_error():
    with pytest.raises(MarketplaceListingError, match="Aussenfarbe"):
        _field_row(_listing(exterior_colour=""), kundennummer="EFAG-042")


def test_missing_odometer_is_a_listing_error():
    # A missing/cleared odometer reading must fail validation the same
    # way a missing colour does — never silently transmit "0 km".
    with pytest.raises(MarketplaceListingError, match="Kilometer"):
        _field_row(_listing(odometer_km=None), kundennummer="EFAG-042")


def test_semicolon_in_free_text_is_sanitised_never_reaches_the_delimiter():
    # §4.3 rule 3: "das verwendete Trennzeichen darf nicht innerhalb der
    # Objektdaten verwendet werden."
    listing = _listing(make="Mercedes; Benz")
    row = _field_row(listing, kundennummer="EFAG-042")
    assert ";" not in row["Marke"]
    assert row["Marke"] == "Mercedes, Benz"


def test_embedded_newline_in_free_text_is_sanitised_never_breaks_the_record_delimiter():
    # The file's own record separator is CRLF (build_feed_file joins rows
    # on "\r\n") — an embedded newline in free text would otherwise split
    # one logical row into two physical lines.
    listing = _listing(body_style="Kombi\nExtra line", make="VW\rW")
    row = _field_row(listing, kundennummer="EFAG-042")
    assert "\n" not in row["Aufbau"]
    assert "\r" not in row["Marke"]


def test_build_feed_file_is_a_header_row_plus_one_semicolon_terminated_row_per_listing():
    payload = build_feed_file([_listing()], kundennummer="EFAG-042")
    text = payload.decode("utf-8")
    lines = text.strip("\r\n").split("\r\n")
    assert len(lines) == 2
    assert lines[0].startswith("Kundennummer;FahrzeugNr;")
    assert lines[0].endswith(";")
    assert lines[1].endswith(";")
    assert "EFAG-042" in lines[1]


def test_build_feed_file_with_one_bad_listing_raises_before_any_row_is_built_for_the_others():
    # The whole file build fails atomically — never a file with N-1 rows
    # silently missing the bad one (that would unpublish it at AS24i).
    good = _listing()
    bad = _listing(stock_item_id="22222222-2222-2222-2222-222222222222", exterior_colour="")
    with pytest.raises(MarketplaceListingError):
        build_feed_file([good, bad], kundennummer="EFAG-042")


def test_build_feed_file_with_zero_listings_is_still_a_valid_header_only_file():
    # AS24i's own full-delivery semantics: a delivery with nothing
    # published is a legitimate "delete everything" file, not a no-op.
    payload = build_feed_file([], kundennummer="EFAG-042")
    text = payload.decode("utf-8")
    lines = text.strip("\r\n").split("\r\n")
    assert len(lines) == 1
    assert lines[0].startswith("Kundennummer;")


# --- AutoScout24Adapter (FTP transport) -------------------------------------


class _FakeConnection:
    def __init__(self, config):
        self.config = config
        self.id = "connection-1"


def test_transmit_feed_uploads_via_storbinary_to_a_stable_filename():
    ftp = FakeFtpClient()
    connection = _FakeConnection({"kundennummer": "EFAG-042"})
    adapter = AutoScout24Adapter(db=None, connection=connection, ftp_client_factory=lambda: ftp, actor_id=None, purpose="test")

    result = adapter.transmit_feed([_listing()])

    assert result.transmitted_count == 1
    assert len(ftp.stored) == 1
    cmd, payload = ftp.stored[0]
    assert cmd == "STOR autoscout24.txt"
    assert b"EFAG-042" in payload
    assert ftp.quit_called is True


def test_transmit_feed_uses_the_configured_feed_filename_when_set():
    ftp = FakeFtpClient()
    connection = _FakeConnection({"kundennummer": "EFAG-042", "feedFilename": "efag_stock.txt"})
    adapter = AutoScout24Adapter(db=None, connection=connection, ftp_client_factory=lambda: ftp, actor_id=None, purpose="test")

    adapter.transmit_feed([_listing()])

    assert ftp.stored[0][0] == "STOR efag_stock.txt"


def test_transmit_feed_quits_the_ftp_session_even_if_storbinary_raises():
    class RaisingFtpClient(FakeFtpClient):
        def storbinary(self, cmd, fp):
            raise ConnectionError("boom")

    ftp = RaisingFtpClient()
    connection = _FakeConnection({"kundennummer": "EFAG-042"})
    adapter = AutoScout24Adapter(db=None, connection=connection, ftp_client_factory=lambda: ftp, actor_id=None, purpose="test")

    with pytest.raises(MarketplaceTransmissionError, match="boom"):
        adapter.transmit_feed([_listing()])
    assert ftp.quit_called is True


def test_a_raw_transport_error_is_wrapped_never_left_to_propagate_as_is():
    # marketplace_transmission.py's own except clause only recognises
    # MarketplaceTransmissionError (and the gateway's own errors) — a raw
    # ftplib/connection error escaping unwrapped would fall through
    # uncaught instead of marking the affected rows FAILED.
    class TimingOutFtpClient(FakeFtpClient):
        def storbinary(self, cmd, fp):
            raise TimeoutError("connection timed out")

    ftp = TimingOutFtpClient()
    connection = _FakeConnection({"kundennummer": "EFAG-042"})
    adapter = AutoScout24Adapter(db=None, connection=connection, ftp_client_factory=lambda: ftp, actor_id=None, purpose="test")

    with pytest.raises(MarketplaceTransmissionError):
        adapter.transmit_feed([_listing()])


def test_validate_listing_raises_without_ever_constructing_the_ftp_client():
    factory_calls = []

    def ftp_client_factory():
        factory_calls.append(1)
        return FakeFtpClient()

    connection = _FakeConnection({"kundennummer": "EFAG-042"})
    adapter = AutoScout24Adapter(db=None, connection=connection, ftp_client_factory=ftp_client_factory, actor_id=None, purpose="test")

    with pytest.raises(MarketplaceListingError):
        adapter.validate_listing(_listing(exterior_colour=""))
    # No FTP connection was ever opened just to validate.
    assert factory_calls == []

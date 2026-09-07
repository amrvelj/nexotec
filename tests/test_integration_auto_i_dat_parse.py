"""Configurator C-0 / KAN-38 PR 1 — the pure ``Suchen``-response parsing
helpers, against literal XML taken from *Webservice Fahrzeuge*'s own
``Resultat`` blocks. No SOAP, no DB, no ``zeep`` — the same isolation
``aes_decrypt.py``'s test has.
"""

import datetime as dt
from decimal import Decimal

import pytest

from app.integration.adapters.auto_i_dat_parse import (
    AutoIDatResponseError,
    ProviderMaintenanceError,
    ProviderRejectedError,
    csv_list,
    date_ddmmyyyy,
    first_lang_text,
    lang_texts,
    parse_suchen_result,
    row_decimal,
    row_int,
    row_text,
    yyyymm_to_year,
)

# --- Info / Status handling ------------------------------------------------

_FAHRZEUGE_OK = (
    '<?xml version="1.0" encoding="utf-8"?>'
    "<AutoiFahrzeuge>"
    "<Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
    "<Fahrzeuge>"
    "<FzKey>141695</FzKey><FzArt>01</FzArt><Marke>Alfa Romeo</Marke>"
    "<ModKurzBez>Giulietta</ModKurzBez><TypDe>1.4 TB Progression</TypDe>"
    "<ProdVon>201003</ProdVon><ProdBis>201309</ProdBis>"
    "<LetzterNP>26750</LetzterNP><Aufbau>6</Aufbau><Treibstoff>2</Treibstoff>"
    "<Antrieb>2</Antrieb><Getriebe>1</Getriebe><Werkscode>191B51</Werkscode>"
    "<MarkenNr>020</MarkenNr>"
    "</Fahrzeuge>"
    "</AutoiFahrzeuge>"
)


def test_parses_status_and_the_repeating_rows():
    result = parse_suchen_result("Fahrzeuge", _FAHRZEUGE_OK)
    assert result.status == 0
    assert result.status_msg == "OK"
    assert len(result.rows) == 1
    el = result.first
    assert row_text(el, "Marke") == "Alfa Romeo"
    assert row_text(el, "Werkscode") == "191B51"
    assert row_text(el, "MarkenNr") == "020"


def test_status_1_is_maintenance():
    xml = "<AutoiSystem><Info><Status>1</Status><StatusMsg>Wartung</StatusMsg></Info></AutoiSystem>"
    with pytest.raises(ProviderMaintenanceError, match="Wartung"):
        parse_suchen_result("System", xml)


def test_status_2_is_an_empty_result_not_an_error():
    xml = "<AutoiFahrzeuge><Info><Status>2</Status><StatusMsg>Keine Daten gefunden</StatusMsg></Info></AutoiFahrzeuge>"
    result = parse_suchen_result("Fahrzeuge", xml)
    assert result.status == 2
    assert result.rows == []


def test_empty_response_is_a_provider_rejection():
    with pytest.raises(ProviderRejectedError):
        parse_suchen_result("Fahrzeuge", "")
    with pytest.raises(ProviderRejectedError):
        parse_suchen_result("Fahrzeuge", "   ")


def test_malformed_xml_raises_a_response_error():
    with pytest.raises(AutoIDatResponseError, match="not well-formed"):
        parse_suchen_result("Fahrzeuge", "<AutoiFahrzeuge><Info>")


def test_root_prefix_inconsistency_is_tolerated():
    # p30 sample uses <AutoifFzKeyChanged>, not <Autoi...>; the row name is
    # what parsing keys off.
    xml = (
        "<AutoifFzKeyChanged><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FzKeyChanged><FzKeyList>174731,175277,175281</FzKeyList></FzKeyChanged>"
        "</AutoifFzKeyChanged>"
    )
    result = parse_suchen_result("FzKeyChanged", xml)
    assert csv_list(row_text(result.first, "FzKeyList")) == ["174731", "175277", "175281"]


def test_page_and_count_info_when_present():
    xml = (
        "<AutoiFahrzeuge><Info><Status>0</Status><StatusMsg>OK</StatusMsg>"
        "<TotalSeiten>4</TotalSeiten><Anzahl>174</Anzahl></Info></AutoiFahrzeuge>"
    )
    result = parse_suchen_result("Fahrzeuge", xml)
    assert result.total_pages == 4
    assert result.count == 174


# --- field readers ------------------------------------------------------


def test_row_int_and_decimal_tolerate_missing_and_bad_values():
    result = parse_suchen_result("Fahrzeuge", _FAHRZEUGE_OK)
    el = result.first
    assert row_int(el, "LetzterNP") == 26750
    assert row_int(el, "DoesNotExist") is None
    assert row_decimal(el, "LetzterNP") == Decimal(26750)
    assert row_decimal(el, "DoesNotExist") is None


def test_yyyymm_to_year():
    assert yyyymm_to_year("201003") == 2010
    assert yyyymm_to_year("000000") is None
    assert yyyymm_to_year("") is None
    assert yyyymm_to_year(None) is None


def test_date_ddmmyyyy():
    assert date_ddmmyyyy("03.04.2008") == dt.date(2008, 4, 3)
    assert date_ddmmyyyy("00.00.0000") is None
    assert date_ddmmyyyy(None) is None
    assert date_ddmmyyyy("garbage") is None


def test_lang_texts_and_first_lang_text():
    xml = (
        "<AutoiOptionen><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Optionen><BezDe>Klimaanlage</BezDe><BezFr>Climatisation</BezFr><BezIt>Climatizzatore</BezIt></Optionen>"
        "</AutoiOptionen>"
    )
    el = parse_suchen_result("Optionen", xml).first
    assert lang_texts(el, "Bez") == {"de": "Klimaanlage", "fr": "Climatisation", "it": "Climatizzatore"}
    assert first_lang_text(el, "Bez") == "Klimaanlage"
    assert first_lang_text(el, "Bez", prefer=("fr", "de")) == "Climatisation"

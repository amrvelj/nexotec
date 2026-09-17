"""WP-6 PR-3 + Configurator C-0 / KAN-38 PR 1: the real auto-i-dat
adapter.

**Transport reworked (KAN-38 PR 1).** The real webservice is ONE
operation — ``Suchen(Benutzername, Passwort, Sprache, Datenname,
Suchwerte, Einstellungen) -> String`` returning a single AES-encrypted
XML document (*Webservice Fahrzeuge* p2/p4/p35). ``FakeSoapClient`` below
now models that: one ``Suchen`` method, dispatching on ``Datenname`` and
returning ``base64(AES(<spec Resultat XML>))``. The XML fixtures are
lifted from the spec's own ``Resultat`` blocks (pages cited inline).

**What changed vs. the pre-rework tests, and why:**
  * ``FakeSoapClient`` had seven invented per-operation methods
    (``Fahrzeuge``, ``System``, …) returning objects with *invented*
    field names (``MarkeCode``, ``BaujahrVon``, ``StandDatum``,
    ``FzKeys``). Those operations and field names do not exist on the
    WSDL. Replaced with the single ``Suchen`` and the spec's real field
    names.
  * ``test_credential_resolution_is_audit_logged`` asserted exactly one
    ``secret_resolved`` event. The reworked transport decrypts *every*
    response, so it resolves the AES key on every call in addition to the
    password — the test now asserts both.
The credential-in-memory-only, retry, and circuit-breaker tests are
otherwise unchanged: they assert transport *behaviour*, not the fake's
field shape.
"""

import base64
import time
import uuid
from decimal import Decimal

import pytest

from app.core.audit import list_audit_events
from app.integration.adapters import auto_i_dat_soap
from app.integration.adapters.aes_decrypt import decrypt_aes_cbc, encrypt_aes_cbc
from app.integration.adapters.auto_i_dat_parse import ProviderMaintenanceError, ProviderRejectedError
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter, _achsen_code_to_axle, _serialise_suchwerte
from app.integration.models.connection import ConnectionEnvironment
from app.integration.models.provider import IntegrationProvider
from app.integration.schemas.connection import ConnectionCreate
from app.integration.services import connections as connection_service
from app.integration.services import resilience

_AES_KEY = b"0123456789abcdef"  # 16 bytes -> AES-128; matches FakeSecretsBackend below
_IV = b"fedcba9876543210"


def _make_provider(db_session, **overrides) -> IntegrationProvider:
    defaults = {
        "provider_code": "auto_i_dat",
        "category": "vehicle_data",
        "display_name": "auto-i-dat",
        "auth_type": "soap_password_aes",
        "required_secret_slots": ["password", "aes_key"],
        "capability_codes": [
            "fahrzeuge", "optionen", "kontrollschild", "pneu", "bewertung", "vin", "vin_ident_db", "ins_tc",
        ],
    }
    defaults.update(overrides)
    provider = IntegrationProvider(**defaults)
    db_session.add(provider)
    db_session.commit()
    db_session.refresh(provider)
    return provider


def _make_connection(db_session, provider, *, tenant_id=None):
    return connection_service.create_connection(
        db_session,
        tenant_id=tenant_id or uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )


# --- spec Resultat XML (Webservice Fahrzeuge) -------------------------------

_SPEC_XML: dict[str, str] = {
    # p9-10, plus the inlined <Typenscheine> block that Einstellungen=Typenscheine=1 adds.
    "Fahrzeuge": (
        '<?xml version="1.0" encoding="utf-8"?><AutoiFahrzeuge>'
        "<Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Fahrzeuge>"
        "<FzKey>141695</FzKey><FzArt>01</FzArt><MarkenNr>020</MarkenNr><Marke>Alfa Romeo</Marke>"
        "<ModKurzBez>Giulietta</ModKurzBez><TypDe>1.4 TB Progression</TypDe>"
        "<ProdVon>201003</ProdVon><ProdBis>201309</ProdBis><LetzterNP>26750</LetzterNP>"
        "<Aufbau>6</Aufbau><Treibstoff>2</Treibstoff><Antrieb>2</Antrieb><Getriebe>1</Getriebe>"
        "<Werkscode>191B51</Werkscode>"
        "<Typenscheine><TypSchNr>1AA397</TypSchNr><TypSchNr>1AA398</TypSchNr></Typenscheine>"
        "</Fahrzeuge>"
        "</AutoiFahrzeuge>"
    ),
    # p25
    "System": (
        "<AutoiSystem><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<System><ModellJahr>2026</ModellJahr><ModellJahrMoto>2026</ModellJahrMoto>"
        "<UpdateDatum>30.08.2026</UpdateDatum><UpdateDatumMoto>14.02.2026</UpdateDatumMoto></System>"
        "</AutoiSystem>"
    ),
    # p30
    "FzKeyChanged": (
        "<AutoifFzKeyChanged><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FzKeyChanged><FzKeyList>FZ100001,FZ100002,FZ100003</FzKeyList></FzKeyChanged>"
        "</AutoifFzKeyChanged>"
    ),
    # p15
    "Optionen": (
        "<AutoiOptionen><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Optionen><OptKey>100004</OptKey><BezDe>Klimaautomat</BezDe><Inklusiv>0</Inklusiv>"
        "<Preis>2200</Preis><OptCode>B21</OptCode><PackCode>0</PackCode><Gruppe>5</Gruppe><SuchCode>2,7</SuchCode></Optionen>"
        "<Optionen><OptKey>100005</OptKey><BezDe>Metallic-Lackierung</BezDe><Inklusiv>0</Inklusiv>"
        "<Preis>900</Preis><OptCode>MET</OptCode><PackCode>0</PackCode><Gruppe>2</Gruppe><SuchCode>9</SuchCode></Optionen>"
        "<Optionen><OptKey>100006</OptKey><BezDe>Winterpaket</BezDe><Inklusiv>1</Inklusiv>"
        "<Preis>0</Preis><OptCode>WNTR</OptCode><PackCode>5</PackCode><Gruppe>1</Gruppe><SuchCode></SuchCode></Optionen>"
        "</AutoiOptionen>"
    ),
    # p20
    "OptionenFarben": (
        "<AutoiOptionenFarben><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<OptionenFarben><OptCode>9H</OptCode><FarbArt>1</FarbArt><BezDe>Black Cherry</BezDe><Preis>500</Preis></OptionenFarben>"
        "<OptionenFarben><OptCode>ST</OptCode><FarbArt>2</FarbArt><BezDe>Stoff Schwarz</BezDe><Preis>0</Preis></OptionenFarben>"
        "</AutoiOptionenFarben>"
    ),
    # p21
    "PneuDimTS": (
        "<AutoiPneuDimTS><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<PneuDimTS><TypSchNr>1MD448</TypSchNr><PneuTyp>121</PneuTyp><Getriebe>1</Getriebe>"
        "<AchsenCode>2</AchsenCode><Dimension>245/40 R18 V</Dimension><BemDe>nur mit Leichtmetallfelgen</BemDe></PneuDimTS>"
        "<PneuDimTS><TypSchNr>1MD448</TypSchNr><PneuTyp>121</PneuTyp><Getriebe>1</Getriebe>"
        "<AchsenCode>3</AchsenCode><Dimension>245/40 R18 V</Dimension><BemDe></BemDe></PneuDimTS>"
        "</AutoiPneuDimTS>"
    ),
    # p23
    "Bilder": (
        "<AutoiBilder><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Bilder><BildURL>https://images.autoi.ch/img/601071S.jpg</BildURL><BildTyp>S</BildTyp><BildArt>A</BildArt></Bilder>"
        "<Bilder><BildURL>https://images.autoi.ch/img/601071I.jpg</BildURL><BildTyp>S</BildTyp><BildArt>I</BildArt></Bilder>"
        "</AutoiBilder>"
    ),
    # -- Configurator C-0 / KAN-38 PR 2 — the fourteen new Datennamen ----
    # p5
    "FahrzeugArten": (
        "<AutoiFahrzeugArten><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FahrzeugArten><FzArt>01</FzArt><BezDe>Personenwagen</BezDe></FahrzeugArten>"
        "</AutoiFahrzeugArten>"
    ),
    # p6
    "Marken": (
        "<AutoiMarken><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Marken><MarkenNr>020</MarkenNr><Marke>Alfa Romeo</Marke></Marken>"
        "</AutoiMarken>"
    ),
    # p7 — note ProdVon/ProdBis here are plain 4-digit years, unlike Fahrzeuge's own 6-digit JJJJMM.
    "ModellGruppen": (
        "<AutoiModellGruppen><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<ModellGruppen><ModGrpKey>101552</ModGrpKey><ModBezDe>156 Limousine</ModBezDe>"
        "<ModKurzBez>156</ModKurzBez><ProdVon>1995</ProdVon><ProdBis>2004</ProdBis></ModellGruppen>"
        "</AutoiModellGruppen>"
    ),
    # p8
    "ModellGruppenKurz": (
        "<AutoiModellGruppenKurz><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<ModellGruppenKurz><MarkenNr>020</MarkenNr><Marke>Alfa Romeo</Marke><ModKurzBez>156</ModKurzBez></ModellGruppenKurz>"
        "</AutoiModellGruppenKurz>"
    ),
    # p11
    "FahrzeugePreise": (
        "<AutoiFahrzeugePreise><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FahrzeugePreise><Jahr>2010</Jahr><Preis>28500</Preis></FahrzeugePreise>"
        "</AutoiFahrzeugePreise>"
    ),
    # p12 — the row's own field name IS the Gruppiert dimension asked for.
    "FzgWerteGruppiert": (
        "<AutoiFzgWerteGruppiert><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FzgWerteGruppiert><Aufbau>6</Aufbau></FzgWerteGruppiert>"
        "</AutoiFzgWerteGruppiert>"
    ),
    # p13
    "Typenscheine": (
        "<AutoiTypenscheine><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Typenscheine><TypSchNr>1AA397</TypSchNr></Typenscheine>"
        "</AutoiTypenscheine>"
    ),
    # p14
    "KontrollschildInfo": (
        "<AutoiKontrollschildInfo><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<KontrollschildInfo><FzArt>01</FzArt><Marke>Subaru</Marke><ModBezDe>G3X Justy Limousine</ModBezDe>"
        "<ProdVon>2003</ProdVon><ProdBis>2009</ProdBis><TypSchNr>1SC653</TypSchNr>"
        "<ErstIVDatum>03.10.2003</ErstIVDatum><StammNr>626702193</StammNr></KontrollschildInfo>"
        "</AutoiKontrollschildInfo>"
    ),
    # p27 — the spec's own example misspells the row element
    # <FarhrzeugeMatch> (transposed r/h); MatchCode lives in Info.
    "FahrzeugeMatch": (
        "<AutoiFahrzeugeMatch><Info><Status>0</Status><StatusMsg>OK</StatusMsg><MatchCode>2</MatchCode></Info>"
        "<FarhrzeugeMatch><FzKey>141695</FzKey><FzArt>01</FzArt><MarkenNr>020</MarkenNr><Marke>Alfa Romeo</Marke>"
        "<ModKurzBez>Giulietta</ModKurzBez><TypDe>1.4 TB Progression</TypDe>"
        "<ProdVon>201003</ProdVon><ProdBis>201309</ProdBis><LetzterNP>26750</LetzterNP>"
        "</FarhrzeugeMatch></AutoiFahrzeugeMatch>"
    ),
    # p28
    "FzgDatenTS": (
        "<AutoiFzgDatenTS><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<FzgDatenTS><EuroNorm>6b</EuroNorm><VerbMix>3.9</VerbMix><VerbNorm>3</VerbNorm><VerbKat>A</VerbKat>"
        "<CO2>103</CO2><GewLeer>1537</GewLeer><EnergieVerbrauch>0</EnergieVerbrauch></FzgDatenTS>"
        "</AutoiFzgDatenTS>"
    ),
    # p17
    "OptionenPack": (
        "<AutoiOptionenPack><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<OptionenPack><OptKey>100369</OptKey><BezDe>Höhenverstellbarer Beifahrersitz</BezDe></OptionenPack>"
        "</AutoiOptionenPack>"
    ),
    # p18
    "OptionenAusschluss": (
        "<AutoiOptionenAusschluss><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<OptionenAusschluss><OptKey>100017</OptKey></OptionenAusschluss>"
        "</AutoiOptionenAusschluss>"
    ),
    # p19
    "OptionenZusatz": (
        "<AutoiOptionenZusatz><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<OptionenZusatz><OptKey>108181</OptKey><BezDe>Pack Family</BezDe><Aktion>1</Aktion><Preis>0</Preis></OptionenZusatz>"
        "</AutoiOptionenZusatz>"
    ),
    # p24
    "Codes": (
        "<AutoiCodes><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
        "<Codes><CodeGrpNr>010</CodeGrpNr><CodeNr>1</CodeNr><BezDe>Geländewagen/SUV</BezDe><BezKurzDe>Gpw</BezKurzDe></Codes>"
        "</AutoiCodes>"
    ),
}


def _encrypt_for_wire(xml: str) -> str:
    return base64.b64encode(encrypt_aes_cbc(xml.encode("utf-8"), key=_AES_KEY, iv=_IV)).decode("ascii")


class FakeSoapClient:
    """One ``Suchen`` operation, matching the real WSDL. ``fail_times``
    makes the first N calls raise (the timeout/retry path). ``responses``
    overrides the per-Datenname XML; anything missing yields a
    ``Status=2`` ("keine Daten") document.
    """

    def __init__(self, *, fail_times: int = 0, responses: dict[str, str | None] | None = None):
        self.calls: list[dict[str, str]] = []
        self._fail_times = fail_times
        self._responses: dict[str, str | None] = {**_SPEC_XML, **(responses or {})}

    def Suchen(self, *, Benutzername, Passwort, Sprache, Datenname, Suchwerte, Einstellungen) -> str:
        self.calls.append(
            {
                "Benutzername": Benutzername,
                "Sprache": Sprache,
                "Datenname": Datenname,
                "Suchwerte": Suchwerte,
                "Einstellungen": Einstellungen,
            }
        )
        if len(self.calls) <= self._fail_times:
            raise ConnectionError("simulated transient SOAP failure")
        xml = self._responses.get(Datenname)
        if xml is None:
            xml = (
                f"<Autoi{Datenname}><Info><Status>2</Status>"
                f"<StatusMsg>Keine Daten gefunden</StatusMsg></Info></Autoi{Datenname}>"
            )
        return _encrypt_for_wire(xml)


class FakeSecretsBackend:
    """In-memory stand-in for services/secrets_backend.py."""

    def __init__(self, values: dict[tuple[uuid.UUID, str], str]):
        self._values = values

    def resolve_secret(self, *, connection_id: uuid.UUID, slot: str) -> str:
        return self._values[(connection_id, slot)]


def _adapter(db_session, connection, *, soap_client=None, actor_id=None, purpose="vehicle_data", monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(
            auto_i_dat_soap,
            "secrets_backend",
            FakeSecretsBackend(
                {(connection.id, "password"): "s3cret", (connection.id, "aes_key"): _AES_KEY.decode("ascii")}
            ),
        )
    return AutoIDatSoapAdapter(
        db=db_session,
        connection=connection,
        soap_client=soap_client or FakeSoapClient(),
        actor_id=actor_id or uuid.uuid4(),
        purpose=purpose,
    )


# --- _serialise_suchwerte (Webservice Fahrzeuge p3) -----------------------


def test_serialise_suchwerte_pairs_and_multi_values():
    assert _serialise_suchwerte({"FzArt": "01", "MarkenNr": "020"}) == "FzArt=01;MarkenNr=020"
    assert _serialise_suchwerte({"Treibstoff": ["3", "4"]}) == "Treibstoff=3+4"
    assert _serialise_suchwerte({"FzArt": "01", "Treibstoff": ["3", "4"]}) == "FzArt=01;Treibstoff=3+4"
    assert _serialise_suchwerte(None) == ""
    assert _serialise_suchwerte({}) == ""


def test_serialise_suchwerte_never_touches_key_case():
    # "Fzart=01 falsch, FzArt=01 richtig" — the serialiser passes the
    # caller's casing through verbatim.
    assert _serialise_suchwerte({"FzArt": "01"}) == "FzArt=01"
    assert _serialise_suchwerte({"Fzart": "01"}) == "Fzart=01"


# --- _achsen_code_to_axle (KAN-43 / FR-C-08: "front / rear / both / variants")


def test_achsen_code_to_axle_maps_the_documented_codes():
    assert _achsen_code_to_axle("1") == "both"
    assert _achsen_code_to_axle("2") == "front"
    assert _achsen_code_to_axle("3") == "rear"


def test_achsen_code_to_axle_keeps_undocumented_codes_distinct_rather_than_bucketing_them():
    # Codes 4-7 are undocumented manufacturer-specific variants — collapsing
    # them to one shared bucket would recreate the exact "1"/"2" collision
    # this ticket fixes, just for a different pair of codes.
    assert _achsen_code_to_axle("4") == "variant_4"
    assert _achsen_code_to_axle("5") == "variant_5"
    assert _achsen_code_to_axle("4") != _achsen_code_to_axle("5")


def test_achsen_code_to_axle_defaults_to_front_when_missing():
    assert _achsen_code_to_axle(None) == "front"


# --- AES decrypt (unchanged — pure) --------------------------------------


def test_aes_roundtrip_against_a_self_constructed_vector():
    plaintext = b"a real vendor sample would go here"
    ciphertext = encrypt_aes_cbc(plaintext, key=_AES_KEY, iv=_IV)
    assert decrypt_aes_cbc(ciphertext, key=_AES_KEY) == plaintext


def test_aes_decrypt_rejects_a_wrong_length_key():
    with pytest.raises(ValueError, match="16, 24 or 32 bytes"):
        decrypt_aes_cbc(b"x" * 32, key=b"too-short")


def test_aes_decrypt_rejects_ciphertext_too_short_for_an_iv():
    with pytest.raises(ValueError, match="too short"):
        decrypt_aes_cbc(b"short", key=_AES_KEY)


# --- the whole response is decrypted, then parsed -----------------------


def test_fetch_vehicle_master_data_decrypts_and_reads_the_real_field_names(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    master = adapter.fetch_vehicle_master_data("141695")

    assert master.brand_code == "020"  # MarkenNr, not the old invented "MarkeCode"
    assert master.brand_display_name == "Alfa Romeo"
    assert master.model_group_name == "Giulietta"  # ModKurzBez
    assert master.variant_name == "1.4 TB Progression"  # TypDe
    assert master.model_year_from == 2010  # ProdVon 201003 -> year
    assert master.model_year_to == 2013  # ProdBis 201309 -> year
    assert master.body_style_code == "6"  # Aufbau, not "Karosserie"
    assert master.base_price is not None and str(master.base_price) == "26750"  # LetzterNP, not "Preis"
    assert master.werkscode == "191B51"
    assert master.type_approval_numbers == ["1AA397", "1AA398"]


def test_suchwerte_and_einstellungen_reach_the_wire_as_dsl_strings(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    adapter.fetch_vehicle_master_data("141695")

    call = soap.calls[-1]
    assert call["Datenname"] == "Fahrzeuge"
    assert call["Suchwerte"] == "FzKey=141695"
    assert call["Einstellungen"] == "Typenscheine=1"
    assert call["Sprache"] == auto_i_dat_soap.DEFAULT_SPRACHE


def test_list_changed_keys_parses_one_comma_joined_list_and_formats_the_date(db_session, monkeypatch):
    import datetime as dt

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    keys = adapter.list_changed_keys(since=dt.date(2026, 9, 1))

    assert keys == ["FZ100001", "FZ100002", "FZ100003"]
    assert soap.calls[-1]["Suchwerte"] == "ChangedSince=01.09.2026"  # TT.MM.JJJJ, not ISO


def test_get_system_watermark_reads_modelljahr_and_updatedatum(db_session, monkeypatch):
    import datetime as dt

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    watermark = adapter.get_system_watermark()

    assert watermark.current_model_year == 2026
    assert watermark.update_date == dt.date(2026, 8, 30)


def test_fetch_options_requires_and_sends_the_model_year(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    options = adapter.fetch_options("141695", model_year=2012)

    assert soap.calls[-1]["Suchwerte"] == "FzKey=141695;Jahr=2012"
    assert {o.option_code for o in options} == {"B21", "MET", "WNTR"}
    by_code = {o.option_code: o for o in options}
    assert by_code["B21"].description == "Klimaautomat"
    # KAN-43 — Inklusiv/PackCode/SuchCode, parsed by no one until now.
    assert by_code["B21"].is_included is False
    assert by_code["B21"].is_package is False
    assert by_code["B21"].equipment_feature_codes == ["2", "7"]  # comma-separated at the provider
    assert by_code["WNTR"].is_included is True
    assert by_code["WNTR"].is_package is True  # PackCode "5" != "0"
    assert by_code["WNTR"].equipment_feature_codes == []  # empty SuchCode element


def test_fetch_colours_searches_on_werkscode_not_fz_key(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    colours = adapter.fetch_colours(werkscode="191B51")

    assert soap.calls[-1]["Datenname"] == "OptionenFarben"
    assert soap.calls[-1]["Suchwerte"] == "Werkscode=191B51"
    assert {c.colour_code: c.colour_type for c in colours} == {"9H": "exterior", "ST": "interior"}
    # KAN-43 / FR-C-07 — the surcharge, parsed by no one until now.
    assert {c.colour_code: c.price for c in colours} == {"9H": Decimal(500), "ST": Decimal(0)}


def test_fetch_tyre_specs_searches_on_type_approval_number(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    tyres = adapter.fetch_tyre_specs(type_approval_number="1MD448")

    assert soap.calls[-1]["Suchwerte"] == "TypSchNr=1MD448"
    assert [t.axle for t in tyres] == ["front", "rear"]
    assert tyres[0].size == "245/40 R18 V"
    # KAN-43 / FR-C-08 — PneuTyp/BemDe, parsed by no one until now; an
    # empty <BemDe/> element is None, not "".
    assert [t.season for t in tyres] == ["summer", "summer"]
    assert tyres[0].remark == "nur mit Leichtmetallfelgen"
    assert tyres[1].remark is None


def test_fetch_images_reads_bildurl(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    images = adapter.fetch_images("141695")

    assert [i.image_key for i in images] == ["601071S.jpg", "601071I.jpg"]
    assert [i.bild_art for i in images] == ["A", "I"]
    # KAN-43 — the full URL, previously discarded entirely.
    assert [i.image_url for i in images] == [
        "https://images.autoi.ch/img/601071S.jpg",
        "https://images.autoi.ch/img/601071I.jpg",
    ]


def test_status_2_yields_an_empty_result_not_an_error(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient(responses={"Optionen": None})  # falls through to a Status=2 doc
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    assert adapter.fetch_options("999", model_year=2020) == []


def test_status_1_is_maintenance(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient(
        responses={"System": "<AutoiSystem><Info><Status>1</Status><StatusMsg>Wartung</StatusMsg></Info></AutoiSystem>"}
    )
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    with pytest.raises(ProviderMaintenanceError):
        adapter.get_system_watermark()


def test_empty_provider_response_is_a_rejection(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)

    class _EmptyStringClient:
        def Suchen(self, **kwargs):
            return ""

    adapter = _adapter(db_session, connection, soap_client=_EmptyStringClient(), monkeypatch=monkeypatch)
    with pytest.raises(ProviderRejectedError):
        adapter.get_system_watermark()


# --- the fourteen new Datennamen (Configurator C-0 / KAN-38 PR 2) --------


def test_list_vehicle_kinds(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    kinds = adapter.list_vehicle_kinds()

    assert [(k.code, k.label) for k in kinds] == [("01", "Personenwagen")]


def test_list_brands_requires_fz_art(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    brands = adapter.list_brands(fz_art="01")

    assert soap.calls[-1]["Suchwerte"] == "FzArt=01"
    assert [(b.code, b.name) for b in brands] == [("020", "Alfa Romeo")]


def test_list_model_groups_reads_four_digit_years_not_yyyymm(db_session, monkeypatch):
    # ModellGruppen's ProdVon/ProdBis are 4-digit years (p7) — a genuinely
    # different convention from Fahrzeuge's own 6-digit JJJJMM ProdVon/
    # ProdBis, which is why year4() exists as a separate helper from
    # yyyymm_to_year().
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    groups = adapter.list_model_groups(fz_art="01", marken_nr="020")

    assert len(groups) == 1
    group = groups[0]
    assert group.model_group_key == 101552
    assert group.name_de == "156 Limousine"
    assert group.production_from == 1995  # not 199500 or some YYYYMM misparse
    assert group.production_to == 2004


def test_list_model_groups_short_omits_production_years(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    groups = adapter.list_model_groups_short(fz_art="01")

    assert [(g.brand_code, g.brand_name, g.short_name) for g in groups] == [("020", "Alfa Romeo", "156")]


def test_search_vehicles_is_a_parameter_widening_not_a_new_datenname(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    results = adapter.search_vehicles({"TypSchNr": "1AA397"})

    assert soap.calls[-1]["Datenname"] == "Fahrzeuge"  # not a separate operation
    assert soap.calls[-1]["Suchwerte"] == "TypSchNr=1AA397"
    assert len(results) == 1
    assert results[0].fz_key == "141695"  # read from the row, not an input parameter


def test_fetch_vehicle_prices_by_year(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    prices = adapter.fetch_vehicle_prices("136838", year=2010)

    assert soap.calls[-1]["Suchwerte"] == "FzKey=136838;Jahr=2010"
    assert [(p.year, p.price) for p in prices] == [(2010, 28500)]


def test_fetch_grouped_values_reads_the_dimension_named_field_and_is_never_a_facet_source(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    values = adapter.fetch_grouped_values(fz_art="01", marke="Alfa Romeo", gruppiert="Aufbau")

    assert soap.calls[-1]["Einstellungen"] == "Gruppiert=Aufbau"
    assert values == ["6"]


def test_fetch_type_approvals_returns_every_row(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    assert adapter.fetch_type_approvals("136838") == ["1AA397"]


def test_lookup_plate_never_assumes_a_single_row(db_session, monkeypatch):
    # A Wechselschild, or the same plate on a car and a motorcycle, can
    # legitimately return more than one row (p14) — the adapter must
    # never call .first here.
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient(
        responses={
            "KontrollschildInfo": (
                "<AutoiKontrollschildInfo><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
                "<KontrollschildInfo><FzArt>01</FzArt><Marke>A</Marke><ModBezDe>X</ModBezDe>"
                "<TypSchNr>1</TypSchNr><StammNr>1</StammNr></KontrollschildInfo>"
                "<KontrollschildInfo><FzArt>03</FzArt><Marke>B</Marke><ModBezDe>Y</ModBezDe>"
                "<TypSchNr>2</TypSchNr><StammNr>2</StammNr></KontrollschildInfo>"
                "</AutoiKontrollschildInfo>"
            )
        }
    )
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    results = adapter.lookup_plate("ZH123456")

    assert len(results) == 2
    assert [r.vehicle_kind_code for r in results] == ["01", "03"]


def test_lookup_plate_parses_erstivdatum_and_stammnr(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    results = adapter.lookup_plate("ZH123456", fz_art="01")

    assert soap.calls[-1]["Suchwerte"] == "Kontrollschild=ZH123456;FzArt=01"
    assert len(results) == 1
    plate = results[0]
    assert plate.first_registration_date is not None
    assert plate.first_registration_date.isoformat() == "2003-10-03"
    assert plate.stammnummer == "626702193"
    assert plate.production_from == 2003
    assert plate.production_to == 2009


def test_find_best_match_reads_match_code_from_the_info_block_not_a_row_field(db_session, monkeypatch):
    # p27's own example misspells the row element <FarhrzeugeMatch> — this
    # is the one test that actually exercises that alias.
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    result = adapter.find_best_match(typ_sch_nr="1AA435", neupreis=25850)

    assert result.match_code == 2  # bestmöglich — never applied silently by this adapter
    assert result.vehicle.fz_key == "141695"
    assert result.vehicle.brand_display_name == "Alfa Romeo"


def test_fetch_type_approval_data_only_euronorm_with_typ_sch_nr_alone(db_session, monkeypatch):
    # "Bei Suche mit TypSchNr wird nur die EuroNorm zurückgegeben" (p28) —
    # this is a real-server behaviour the adapter cannot fabricate; it
    # just passes through whatever the response actually contains.
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient(
        responses={
            "FzgDatenTS": (
                "<AutoiFzgDatenTS><Info><Status>0</Status><StatusMsg>OK</StatusMsg></Info>"
                "<FzgDatenTS><EuroNorm>6b</EuroNorm></FzgDatenTS></AutoiFzgDatenTS>"
            )
        }
    )
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    result = adapter.fetch_type_approval_data("1AA549")

    assert result.euro_norm == "6b"
    assert result.co2 is None


def test_fetch_type_approval_data_full_shape_with_getriebe_and_gaenge(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    result = adapter.fetch_type_approval_data("1AA549", getriebe="2", gaenge=6)

    assert soap.calls[-1]["Suchwerte"] == "TypSchNr=1AA549;Getriebe=2;Gänge=6"
    assert result.euro_norm == "6b"
    assert result.fuel_consumption_mixed == Decimal("3.9")
    assert result.co2 == 103
    assert result.kerb_weight == 1537


def test_fetch_option_package_contents(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    contents = adapter.fetch_option_package_contents(113115)

    assert soap.calls[-1]["Suchwerte"] == "OptKey=113115"
    assert [(c.opt_key, c.description) for c in contents] == [(100369, "Höhenverstellbarer Beifahrersitz")]


def test_fetch_option_exclusions(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    excluded = adapter.fetch_option_exclusions("136838", year=2010, opt_key=113115)

    assert soap.calls[-1]["Suchwerte"] == "FzKey=136838;Jahr=2010;OptKey=113115"
    assert excluded == [100017]


def test_fetch_option_conditions_reads_aktion_code(db_session, monkeypatch):
    # Aktion (CodeGrpNr 047) is ADR-072's own option_relation_type
    # canonical list — this adapter hands back the raw code, unresolved.
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    conditions = adapter.fetch_option_conditions("120963", year=2010, opt_key=114683)

    assert [(c.opt_key, c.description, c.aktion_code) for c in conditions] == [(108181, "Pack Family", "1")]


def test_fetch_codes_filters_by_code_group_when_asked(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    codes = adapter.fetch_codes(code_groups=["010", "011", "012", "013"])

    assert soap.calls[-1]["Suchwerte"] == "CodeGrpNr=010+011+012+013"
    assert [(c.code_group_nr, c.code_nr, c.label_de) for c in codes] == [("010", "1", "Geländewagen/SUV")]


def test_fetch_codes_active_only_sends_status_1(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    adapter.fetch_codes(active_only=True)

    assert soap.calls[-1]["Suchwerte"] == "Status=1"


# --- credential resolution: in-memory only, audited -----------------------


def test_credential_resolution_is_audit_logged_with_actor_tenant_connection_purpose(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    actor_id = uuid.uuid4()
    adapter = _adapter(db_session, connection, actor_id=actor_id, monkeypatch=monkeypatch)

    adapter.fetch_vehicle_master_data("141695")

    events = list_audit_events(
        db_session, entity_type="integration_secret_ref", entity_id=connection.id, tenant_id=connection.tenant_id
    )
    # The reworked transport decrypts every response, so it resolves BOTH
    # the password (for the SOAP call) and the AES key (for decrypt).
    resolved_slots = {e.reason.split("slot=")[1].split(" ")[0] for e in events}
    assert resolved_slots == {"password", "aes_key"}
    for event in events:
        assert event.action == "secret_resolved"
        assert event.actor_id == actor_id
        assert event.tenant_id == connection.tenant_id
        assert "purpose=vehicle_data" in event.reason


def test_credential_resolution_never_persists_the_secret_value_anywhere(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    monkeypatch.setattr(
        auto_i_dat_soap,
        "secrets_backend",
        FakeSecretsBackend(
            {(connection.id, "password"): "s3cret-value", (connection.id, "aes_key"): _AES_KEY.decode("ascii")}
        ),
    )
    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=FakeSoapClient(), actor_id=uuid.uuid4(), purpose="vehicle_data"
    )
    adapter.fetch_vehicle_master_data("141695")

    events = list_audit_events(
        db_session, entity_type="integration_secret_ref", entity_id=connection.id, tenant_id=connection.tenant_id
    )
    for event in events:
        assert "s3cret-value" not in (event.reason or "")
        assert _AES_KEY.decode("ascii") not in (event.reason or "")
    assert not hasattr(adapter, "_password_cache")


def test_resolve_secret_not_exported_from_public():
    from app.integration import public

    assert "resolve_secret" not in public.__all__
    assert not hasattr(public, "resolve_secret")


# --- timeout + one retry with jitter -----------------------------------


def test_soap_call_retries_once_on_transient_failure_then_succeeds(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap_client = FakeSoapClient(fail_times=1)
    adapter = _adapter(db_session, connection, soap_client=soap_client, monkeypatch=monkeypatch)

    result = adapter.fetch_vehicle_master_data("141695")
    assert result.brand_display_name == "Alfa Romeo"
    assert len(soap_client.calls) == 2  # one failure, one retry that succeeded


def test_soap_call_gives_up_after_one_retry_and_raises(db_session, monkeypatch):
    monkeypatch.setattr(resilience, "_JITTER_RANGE_SECONDS", (0.0, 0.0))
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap_client = FakeSoapClient(fail_times=2)
    adapter = _adapter(db_session, connection, soap_client=soap_client, monkeypatch=monkeypatch)

    with pytest.raises(ConnectionError):
        adapter.fetch_vehicle_master_data("141695")
    assert len(soap_client.calls) == 2  # never more than one retry


# --- circuit breaker: opens after the threshold, per connection --------


def test_circuit_breaker_opens_after_the_failure_threshold_and_short_circuits():
    connection_id = uuid.uuid4()
    resilience.reset_circuit(connection_id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_id)
        assert resilience.is_circuit_open(connection_id) is True
    finally:
        resilience.reset_circuit(connection_id)


def test_circuit_breaker_state_is_per_connection_not_global():
    connection_a = uuid.uuid4()
    connection_b = uuid.uuid4()
    resilience.reset_circuit(connection_a)
    resilience.reset_circuit(connection_b)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_a)
        assert resilience.is_circuit_open(connection_a) is True
        assert resilience.is_circuit_open(connection_b) is False
    finally:
        resilience.reset_circuit(connection_a)
        resilience.reset_circuit(connection_b)


def test_circuit_breaker_recovers_half_open_after_the_open_duration(monkeypatch):
    connection_id = uuid.uuid4()
    resilience.reset_circuit(connection_id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection_id)
        assert resilience.is_circuit_open(connection_id) is True

        real_monotonic = time.monotonic
        monkeypatch.setattr(
            resilience.time, "monotonic", lambda: real_monotonic() + resilience._OPEN_DURATION_SECONDS + 1
        )
        assert resilience.is_circuit_open(connection_id) is False
    finally:
        resilience.reset_circuit(connection_id)


def test_gateway_call_capability_refuses_when_circuit_is_open(db_session):
    from app.integration.services import gateway

    provider = _make_provider(db_session, provider_code="auto_i_dat_mock", auth_type="none", required_secret_slots=[])
    connection = connection_service.create_connection(
        db_session,
        tenant_id=uuid.uuid4(),
        data=ConnectionCreate(
            provider_id=provider.id, display_name="auto-i-dat", environment=ConnectionEnvironment.SANDBOX
        ),
        actor_id=uuid.uuid4(),
    )
    resilience.reset_circuit(connection.id)
    try:
        for _ in range(resilience._FAILURE_THRESHOLD):
            resilience.record_failure(connection.id)

        with pytest.raises(resilience.CircuitOpenError), gateway.call_capability(
            db_session, connection=connection, capability="vehicle_data"
        ):
            pass
    finally:
        resilience.reset_circuit(connection.id)


def test_decode_vin_is_unimplemented(db_session):
    """KAN-36 — no auto-i-dat VIN webservice specification exists yet."""

    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = AutoIDatSoapAdapter(
        db=db_session, connection=connection, soap_client=FakeSoapClient(), actor_id=uuid.uuid4(), purpose="vin_decode"
    )
    with pytest.raises(NotImplementedError):
        adapter.decode_vin(vin="WVWZZZ1JZXW000001")

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

import pytest

from app.core.audit import list_audit_events
from app.integration.adapters import auto_i_dat_soap
from app.integration.adapters.aes_decrypt import decrypt_aes_cbc, encrypt_aes_cbc
from app.integration.adapters.auto_i_dat_parse import ProviderMaintenanceError, ProviderRejectedError
from app.integration.adapters.auto_i_dat_soap import AutoIDatSoapAdapter, _serialise_suchwerte
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
        "<Preis>2200</Preis><OptCode>B21</OptCode><PackCode>0</PackCode><Gruppe>5</Gruppe><SuchCode>2</SuchCode></Optionen>"
        "<Optionen><OptKey>100005</OptKey><BezDe>Metallic-Lackierung</BezDe><Inklusiv>0</Inklusiv>"
        "<Preis>900</Preis><OptCode>MET</OptCode><PackCode>0</PackCode><Gruppe>2</Gruppe><SuchCode>9</SuchCode></Optionen>"
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
    assert {o.option_code for o in options} == {"B21", "MET"}
    assert next(o for o in options if o.option_code == "B21").description == "Klimaautomat"


def test_fetch_colours_searches_on_werkscode_not_fz_key(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    colours = adapter.fetch_colours(werkscode="191B51")

    assert soap.calls[-1]["Datenname"] == "OptionenFarben"
    assert soap.calls[-1]["Suchwerte"] == "Werkscode=191B51"
    assert {c.colour_code: c.colour_type for c in colours} == {"9H": "exterior", "ST": "interior"}


def test_fetch_tyre_specs_searches_on_type_approval_number(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    soap = FakeSoapClient()
    adapter = _adapter(db_session, connection, soap_client=soap, monkeypatch=monkeypatch)

    tyres = adapter.fetch_tyre_specs(type_approval_number="1MD448")

    assert soap.calls[-1]["Suchwerte"] == "TypSchNr=1MD448"
    assert [t.axle for t in tyres] == ["front", "rear"]
    assert tyres[0].size == "245/40 R18 V"


def test_fetch_images_reads_bildurl(db_session, monkeypatch):
    provider = _make_provider(db_session)
    connection = _make_connection(db_session, provider)
    adapter = _adapter(db_session, connection, monkeypatch=monkeypatch)

    images = adapter.fetch_images("141695")

    assert [i.image_key for i in images] == ["601071S.jpg", "601071I.jpg"]
    assert [i.bild_art for i in images] == ["A", "I"]


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

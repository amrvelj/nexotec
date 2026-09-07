"""AutoIDatSoapAdapter (WP-6 PR-3; transport reworked in Configurator
C-0 / KAN-38 PR 1) — the real auto-i-dat gateway.

**The real webservice is ONE operation.** *Webservice Fahrzeuge* p2/p35:
``Suchen(Benutzername, Passwort, Sprache, Datenname, Suchwerte,
Einstellungen) -> String``. The 22 Datennamen (``Fahrzeuge``, ``System``,
``KontrollschildInfo``, …) are *values* of the ``Datenname`` parameter,
not separate SOAP operations. An earlier version of this adapter modelled
them as seven distinct operations with pre-parsed response objects — that
never matched the WSDL. It also:

  * passed ``Suchwerte`` as SOAP kwargs, where the real parameter is one
    **case-sensitive** DSL string (``Name=Wert;Name2=Wert1+Wert2``);
  * never sent ``Sprache`` (which is how multi-language labels arrive);
  * decrypted only ``Optionen``'s inner blob, where p4 says the **whole**
    ``SuchenResult`` is one AES-encrypted XML string.

All four are fixed here. The seven wrappers below now serialise their
Suchwerte, go through ``_suchen`` → decrypt-whole-response → parse-XML
(``auto_i_dat_parse.py``), and read the field names the spec's own
``Resultat`` blocks actually use. **None of this has touched a live
account** — ``scripts/verify_auto_i_dat.py`` is the executable check that
runs when the staging account (KAN-38 blocker) exists. Every remaining
uncertainty is on the open-questions list in the PR-1 description.

Credential resolution is IN MEMORY, per call, by this adapter only
(rule 3) — ``_resolve`` never caches a secret on ``self`` beyond the
single call, and every resolution is audit-logged with actor/tenant/
connection/purpose (rule 4) via the generic ``record_audit_event``.

``soap_client`` is injected (never constructed from a bare WSDL URL
inside this class) so the adapter is testable against a hand-written fake
with a matching ``Suchen`` method, no live network call and no real WSDL
file in this repository. ``build_zeep_client`` is the one place a real
WSDL fetch would happen, lazily imported so tests and local dev never pay
zeep's import cost.
"""

import base64
import binascii
import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.integration.adapters.aes_decrypt import decrypt_aes_cbc
from app.integration.adapters.auto_i_dat_parse import (
    SuchenResult,
    csv_list,
    date_ddmmyyyy,
    first_lang_text,
    parse_suchen_result,
    row_decimal,
    row_int,
    row_text,
    yyyymm_to_year,
)
from app.integration.adapters.base import (
    ForecastResult,
    SystemWatermark,
    ValuationResult,
    VariantColourData,
    VariantImageData,
    VariantMasterData,
    VariantOptionData,
    VariantTyreSpecData,
)
from app.integration.models.connection import IntegrationConnection
from app.integration.models.secret_ref import SecretSlot
from app.integration.services import secrets_backend
from app.integration.services.resilience import call_with_retry

# Request every language so no label data is dropped at the transport.
# PR 1's seven wrappers consume the German text only; multi-language
# storage is C-E (ADR-044 — provider text stored as delivered, never
# translated). The spec footnote (p2) says En is currently available only
# for Optionen / OptionenPack / OptionenZusatz — open question whether
# that still holds in 2026.
DEFAULT_SPRACHE = "De+Fr+It+En"

# Suchwerte separators (Webservice Fahrzeuge p3): pairs joined with ";",
# multiple values for one key joined with "+". Keys are case-sensitive
# ("Fzart=01" wrong, "FzArt=01" right) — the wrappers below pass the exact
# spec casing, this serialiser never touches it.
_PAIR_SEP = ";"
_VALUE_SEP = "+"


def _serialise_suchwerte(pairs: Mapping[str, str | int | Sequence[str | int]] | None) -> str:
    if not pairs:
        return ""
    parts: list[str] = []
    for key, value in pairs.items():
        if isinstance(value, (list, tuple)):
            rendered = _VALUE_SEP.join(str(v) for v in value)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return _PAIR_SEP.join(parts)


class SoapClient(Protocol):
    """The one operation of a real ``WebServiceFahrzeuge`` service proxy
    this adapter calls — a structural Protocol, not ``zeep.Client``
    itself, so tests supply a hand-written fake, never a live SOAP
    transport.
    """

    def Suchen(  # external SOAP operation name
        self,
        *,
        Benutzername: str,
        Passwort: str,
        Sprache: str,
        Datenname: str,
        Suchwerte: str,
        Einstellungen: str,
    ) -> str: ...


def build_zeep_client(wsdl_url: str) -> SoapClient:
    import zeep

    return zeep.Client(wsdl_url).service  # type: ignore[return-value]


class AutoIDatSoapAdapter:
    def __init__(
        self,
        *,
        db: Session,
        connection: IntegrationConnection,
        soap_client: SoapClient,
        actor_id: uuid.UUID | None,
        purpose: str,
    ) -> None:
        self._db = db
        self._connection = connection
        self._client = soap_client
        self._actor_id = actor_id
        self._purpose = purpose

    # -- credentials ------------------------------------------------------

    def _resolve(self, slot: SecretSlot) -> str:
        value = secrets_backend.resolve_secret(connection_id=self._connection.id, slot=slot.value)
        record_audit_event(
            self._db,
            entity_type="integration_secret_ref",
            entity_id=self._connection.id,
            tenant_id=self._connection.tenant_id,
            action="secret_resolved",
            actor_id=self._actor_id,
            reason=f"slot={slot.value} purpose={self._purpose}",
        )
        return value

    def _password(self) -> str:
        return self._resolve(SecretSlot.PASSWORD)

    def _aes_key(self) -> bytes:
        return self._resolve(SecretSlot.AES_KEY).encode("utf-8")

    def _decrypt(self, raw: bytes) -> bytes:
        return decrypt_aes_cbc(raw, key=self._aes_key())

    # -- the one SOAP operation ----------------------------------------

    def _suchen(
        self,
        datenname: str,
        suchwerte: Mapping[str, str | int | Sequence[str | int]] | None = None,
        *,
        einstellungen: Mapping[str, str | int] | None = None,
        sprache: str = DEFAULT_SPRACHE,
    ) -> SuchenResult:
        """The single real operation. ``call_with_retry`` applies the
        timeout-with-one-retry-with-jitter layer (``services/resilience.py``);
        the gateway's per-connection circuit breaker wraps the whole
        capability call this method sits inside (``services/gateway.py``).

        The raw string comes back AES-encrypted (p4). base64 is assumed
        around the ciphertext — the spec is silent, and the decryption
        details "arrive with the key", so this is open question #1 for the
        provider; ``_maybe_b64decode`` degrades to raw bytes if the string
        is not valid base64.
        """

        raw: str = call_with_retry(
            lambda: self._client.Suchen(
                Benutzername=self._connection.config.get("username", ""),
                Passwort=self._password(),
                Sprache=sprache,
                Datenname=datenname,
                Suchwerte=_serialise_suchwerte(suchwerte),
                Einstellungen=_serialise_suchwerte(einstellungen),
            )
        )
        if raw is None or not str(raw).strip():
            # p4: an empty string means invalid Benutzername / Passwort /
            # Sprache / Datenname. Caught here, before decrypt, so it
            # surfaces as a rejection rather than an AES "ciphertext too
            # short" error.
            return parse_suchen_result(datenname, "")
        xml_bytes = self._decrypt(_maybe_b64decode(raw))
        return parse_suchen_result(datenname, xml_bytes)

    # -- the seven Datennamen (migrated onto _suchen) -------------------

    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        result = self._suchen("Fahrzeuge", {"FzKey": fz_key}, einstellungen={"Typenscheine": "1"})
        el = result.first
        # `Typenscheine=1` inlines the type-approval numbers; the exact
        # nesting is unconfirmed (open question) — read defensively.
        type_approvals = [t.text.strip() for t in el.findall(".//TypSchNr") if t.text and t.text.strip()]
        return VariantMasterData(
            fz_key=fz_key,
            brand_code=row_text(el, "MarkenNr") or "",
            brand_display_name=row_text(el, "Marke") or "",
            model_group_name=row_text(el, "ModKurzBez") or row_text(el, "ModBezDe") or "",
            variant_name=row_text(el, "TypDe") or "",
            model_year_from=yyyymm_to_year(row_text(el, "ProdVon")) or 0,
            model_year_to=yyyymm_to_year(row_text(el, "ProdBis")),
            vehicle_kind_code=row_text(el, "FzArt") or "",
            fuel_type_code=row_text(el, "Treibstoff"),
            body_style_code=row_text(el, "Aufbau"),
            drivetrain_code=row_text(el, "Antrieb"),
            transmission_code=row_text(el, "Getriebe"),
            base_price=row_decimal(el, "LetzterNP"),
            werkscode=row_text(el, "Werkscode"),
            type_approval_numbers=type_approvals,
        )

    def list_changed_keys(self, *, since: dt.date) -> list[str]:
        # ChangedSince is TT.MM.JJJJ and may not predate today - 3 months
        # (the caller enforces the window; p30). The result is one
        # comma-joined <FzKeyList> string, not repeated elements.
        result = self._suchen("FzKeyChanged", {"ChangedSince": since.strftime("%d.%m.%Y")})
        if not result.rows:
            return []
        return csv_list(row_text(result.first, "FzKeyList"))

    def get_system_watermark(self) -> SystemWatermark:
        result = self._suchen("System")
        el = result.first
        # ModellJahrMoto / UpdateDatumMoto (motorcycles) are a separate
        # watermark the mirror does not track yet — open question.
        return SystemWatermark(
            current_model_year=row_int(el, "ModellJahr") or utcnow().date().year,
            update_date=date_ddmmyyyy(row_text(el, "UpdateDatum")) or utcnow().date(),
        )

    def fetch_options(self, fz_key: str, *, model_year: int) -> list[VariantOptionData]:
        # `Jahr` (Modelljahr) is obligatorisch (p15) — hence the widened
        # signature; the caller (catalogue_sync) holds it on the variant.
        result = self._suchen("Optionen", {"FzKey": fz_key, "Jahr": str(model_year)})
        return [
            VariantOptionData(
                option_code=row_text(row, "OptCode") or "",
                description=first_lang_text(row, "Bez") or "",
                option_group=row_text(row, "Gruppe"),
                price=row_decimal(row, "Preis"),
            )
            for row in result.rows
        ]

    def fetch_colours(self, *, werkscode: str) -> list[VariantColourData]:
        # OptionenFarben keys on Werkscode / Importcode, NOT FzKey (p20),
        # and is only populated for ~15 brands' current models.
        result = self._suchen("OptionenFarben", {"Werkscode": werkscode})
        return [
            VariantColourData(
                colour_code=row_text(row, "OptCode") or "",
                description=first_lang_text(row, "Bez") or "",
                colour_type=_FARB_ART.get(row_text(row, "FarbArt") or "", "exterior"),
            )
            for row in result.rows
        ]

    def fetch_tyre_specs(self, *, type_approval_number: str) -> list[VariantTyreSpecData]:
        # PneuDimTS keys on TypSchNr, NOT FzKey (p21). load_index /
        # speed_rating are embedded in <Dimension> ("245/40 R18 V"), not
        # separate fields — left None pending a parse decision (open q).
        result = self._suchen("PneuDimTS", {"TypSchNr": type_approval_number})
        return [
            VariantTyreSpecData(
                axle=_ACHSEN_CODE.get(row_text(row, "AchsenCode") or "", "front"),
                size=row_text(row, "Dimension") or "",
                load_index=None,
                speed_rating=None,
            )
            for row in result.rows
        ]

    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        # Bilder returns <BildURL> (a URL string), not a key + sequence.
        # ImageRef.image_key is String(160); a URL can exceed that, so the
        # stable short key is the URL basename — the full URL is dropped
        # pending an ImageRef schema change (open question #6).
        result = self._suchen("Bilder", {"FzKey": fz_key})
        images: list[VariantImageData] = []
        for index, row in enumerate(result.rows):
            url = row_text(row, "BildURL") or ""
            images.append(
                VariantImageData(
                    bild_typ=row_text(row, "BildTyp") or "",
                    bild_art=row_text(row, "BildArt") or "",
                    image_key=_image_key_from_url(url),
                    sequence=index,
                )
            )
        return images

    # -- valuation / forecast -----------------------------------------
    #
    # No live caller today (Protocol compliance only). Valuation is
    # actually its own webservice ("Webservice Bewertung.pdf" /
    # "Valuation web service.pdf"), NOT a mode of Fahrzeuge — open
    # question #7. This is a compile-preserving shim onto _suchen until a
    # dedicated Bewertung adapter lands.

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult:
        result = self._suchen(
            "Fahrzeuge",
            {
                "FzKey": fz_key,
                "ErstzulassungsDatum": first_registration.strftime("%d.%m.%Y"),
                "BewertungsDatum": valuation_date.strftime("%d.%m.%Y"),
                "Kilometerstand": str(mileage),
            },
            einstellungen={"Bewertung": "1"},
        )
        el = result.rows[0] if result.rows else None
        return ValuationResult(
            provider_value=row_decimal(el, "Bewertungswert") or Decimal(0),
            status_code=row_text(el, "BewertungsStatus") or "ok",
        )

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult:
        result = self._suchen("Fahrzeuge", {"FzKey": fz_key}, einstellungen={"Forecast": "1"})
        el = result.rows[0] if result.rows else None
        return ForecastResult(
            residual_value=row_decimal(el, "Restwert") or Decimal(0),
            forecast_date=date_ddmmyyyy(row_text(el, "ForecastDatum")) or utcnow().date(),
        )

    def decode_vin(self, *, vin: str) -> Any:
        """KAN-36 — VIN decode is DAT-backed, reached through this auto-
        i-dat account: the DAT sub-account (its own `dat` connection,
        never folded into this one — the two rotate independently) is
        what entitles it, derived as the `vin_decode` capability in
        `services/connections.py::compute_vin_decode_entitlement`, never
        hand-declared. Left unimplemented: no auto-i-dat VIN webservice
        specification exists in Drive today (the four PDFs on file are
        Fahrzeuge, Bewertung, Valuation and Etikette — none documents a
        VIN call), and guessing the wire shape of a real, billed provider
        operation is worse than leaving it absent. Implement this once
        that specification is obtained (KAN-36's own exit criterion).
        """

        raise NotImplementedError("auto-i-dat VIN decode webservice specification is not yet available (KAN-36).")


# -- coded-value helpers (Webservice Fahrzeuge p34) -----------------------

_FARB_ART = {"1": "exterior", "2": "interior"}  # FarbArt: 1 Aussenfarbe, 2 Polsterfarbe
_ACHSEN_CODE = {  # AchsenCode: 1 Vorne+Hinten, 2 Vorne, 3 Hinten, 4-7 Varianten
    "1": "front",
    "2": "front",
    "3": "rear",
}


def _image_key_from_url(url: str) -> str:
    basename = url.rsplit("/", 1)[-1] or url
    return basename[:160]


def _maybe_b64decode(raw: str) -> bytes:
    """The ``Suchen`` result is AES-encrypted text (p4). Whether it is
    base64-wrapped is not documented — decryption details "arrive with the
    key" (open question #1). Try base64; fall back to the raw bytes.
    """

    stripped = raw.strip()
    try:
        return base64.b64decode(stripped, validate=True)
    except (binascii.Error, ValueError):
        return stripped.encode("latin-1", errors="replace")


def _build_real_adapter(
    db: Session, connection: IntegrationConnection, actor_id: uuid.UUID | None, purpose: str
) -> "AutoIDatSoapAdapter":
    """The `_ADAPTER_FACTORIES["auto_i_dat"]` entry (services/gateway.py).
    `wsdlUrl` lives in the connection's own `config` JSON rather than a
    global setting — sandbox and production are separate connections
    (rule 7) and each plausibly has its own WSDL endpoint, so this is the
    one place per-connection config already flows through untouched.
    """

    wsdl_url = connection.config.get("wsdlUrl")
    if not wsdl_url:
        raise ValueError(f"Connection {connection.id} has no config.wsdlUrl set.")
    return AutoIDatSoapAdapter(
        db=db, connection=connection, soap_client=build_zeep_client(wsdl_url), actor_id=actor_id, purpose=purpose
    )


def _register() -> None:
    # Imported lazily to avoid a circular import at module load time —
    # services/gateway.py imports adapters/auto_i_dat_mock.py directly,
    # but nothing in adapters/ should import gateway.py at module scope.
    from app.integration.services.gateway import register_adapter_factory

    register_adapter_factory("auto_i_dat", _build_real_adapter)


_register()

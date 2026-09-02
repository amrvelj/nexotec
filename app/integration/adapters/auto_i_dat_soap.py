"""AutoIDatSoapAdapter (WP-6 PR-3) — the real auto-i-dat gateway: SOAP +
AES response decryption. Implements the same `ProviderAdapter` Protocol
`MockAutoIDatAdapter` (PR-2) does, so `services/gateway.py`'s callers
never know which one they have.

Credential resolution is IN MEMORY, per call, by this adapter only
(rule 3) — `_resolve` never caches a secret value on `self` beyond the
single call that needed it, and every resolution is audit-logged with
actor/tenant/connection/purpose (rule 4), reusing the existing generic
`app.core.audit.record_audit_event` rather than a bespoke audit table.

**Provisional**: this session has no live auto-i-dat WSDL, no real
vendor sample payload, and no confirmed field-level response schema to
build or test against. Operation names (`Fahrzeuge`, `FzKeyChanged`,
`System`, `Optionen`, `OptionenFarben`, `PneuDimTS`, `Bilder`) are the
real operation names named in PRD-Vehicles' own mirror/sync-strategy
table — the response parsing below is a best-effort, defensively-written
mapping onto those names, not a confirmed field-by-field schema. Flag
against the real `SS 03 ... Schnittstellenbeschrieb` spec (in Drive)
before this ever reaches a live account.

`soap_client` is injected (never constructed from a bare WSDL URL inside
this class) specifically so this adapter is testable against a
hand-written fake with matching method names, with no live network call
and no real WSDL file anywhere in this repository. `build_zeep_client`
below is the one place a real WSDL fetch would happen, lazily imported so
every test and most of local dev — which never construct a real adapter
— don't pay zeep's import cost or need the package to run anything else.
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.integration.adapters.aes_decrypt import decrypt_aes_cbc
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


class SoapClient(Protocol):
    """The subset of a real `zeep.Client(...).service` surface this
    adapter calls — a structural Protocol, not `zeep.Client` itself, so
    tests supply a hand-written fake with matching method names, never a
    live SOAP transport.
    """

    def Fahrzeuge(self, **kwargs: Any) -> Any: ...
    def FzKeyChanged(self, **kwargs: Any) -> Any: ...
    def System(self, **kwargs: Any) -> Any: ...
    def Optionen(self, **kwargs: Any) -> Any: ...
    def OptionenFarben(self, **kwargs: Any) -> Any: ...
    def PneuDimTS(self, **kwargs: Any) -> Any: ...
    def Bilder(self, **kwargs: Any) -> Any: ...


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

    def _call(self, operation: str, **kwargs: Any) -> Any:
        """Every operation goes through here — the one place `call_with_
        retry`'s timeout-with-one-retry-with-jitter applies, and the one
        place the password is attached. The gateway's own circuit breaker
        (services/gateway.py) wraps the WHOLE capability call this
        adapter method sits inside, not each individual SOAP operation —
        this retry is the narrower, SOAP-specific layer beneath it.
        """

        client_method = getattr(self._client, operation)
        return call_with_retry(lambda: client_method(username=self._connection.config.get("username"), password=self._password(), **kwargs))

    def fetch_vehicle_master_data(self, fz_key: str) -> VariantMasterData:
        response = self._call("Fahrzeuge", FzKey=fz_key)
        return VariantMasterData(
            fz_key=fz_key,
            brand_code=str(getattr(response, "MarkeCode", "")),
            brand_display_name=str(getattr(response, "Marke", "")),
            model_group_name=str(getattr(response, "ModellGruppe", "")),
            variant_name=str(getattr(response, "Typ", "")),
            model_year_from=int(getattr(response, "BaujahrVon", 0) or 0),
            model_year_to=(int(response.BaujahrBis) if getattr(response, "BaujahrBis", None) else None),
            vehicle_kind_code=str(getattr(response, "FzArt", "")),
            fuel_type_code=(str(response.Treibstoff) if getattr(response, "Treibstoff", None) else None),
            body_style_code=(str(response.Karosserie) if getattr(response, "Karosserie", None) else None),
            drivetrain_code=(str(response.Antrieb) if getattr(response, "Antrieb", None) else None),
            transmission_code=(str(response.Getriebe) if getattr(response, "Getriebe", None) else None),
            base_price=(Decimal(str(response.Preis)) if getattr(response, "Preis", None) else None),
            type_approval_numbers=list(getattr(response, "Typenscheine", []) or []),
        )

    def list_changed_keys(self, *, since: dt.date) -> list[str]:
        response = self._call("FzKeyChanged", ChangedSince=since.isoformat())
        return [str(key) for key in (getattr(response, "FzKeys", None) or [])]

    def get_system_watermark(self) -> SystemWatermark:
        response = self._call("System")
        return SystemWatermark(
            current_model_year=int(getattr(response, "AktuellesModelljahr", utcnow().date().year)),
            update_date=dt.date.fromisoformat(str(getattr(response, "StandDatum", utcnow().date().isoformat()))),
        )

    def fetch_options(self, fz_key: str) -> list[VariantOptionData]:
        response = self._call("Optionen", FzKey=fz_key)
        raw_encrypted = getattr(response, "VerschluesselteDaten", None)
        if raw_encrypted:
            self._decrypt(raw_encrypted)  # decrypted, provisional shape not yet parsed further
        return [
            VariantOptionData(
                option_code=str(getattr(item, "Code", "")),
                description=str(getattr(item, "Bezeichnung", "")),
                option_group=(str(item.Gruppe) if getattr(item, "Gruppe", None) else None),
                price=(Decimal(str(item.Preis)) if getattr(item, "Preis", None) else None),
            )
            for item in (getattr(response, "Optionen", None) or [])
        ]

    def fetch_colours(self, fz_key: str) -> list[VariantColourData]:
        response = self._call("OptionenFarben", FzKey=fz_key)
        return [
            VariantColourData(
                colour_code=str(getattr(item, "Code", "")),
                description=str(getattr(item, "Bezeichnung", "")),
                colour_type=str(getattr(item, "Typ", "exterior")),
            )
            for item in (getattr(response, "Farben", None) or [])
        ]

    def fetch_tyre_specs(self, fz_key: str) -> list[VariantTyreSpecData]:
        response = self._call("PneuDimTS", FzKey=fz_key)
        return [
            VariantTyreSpecData(
                axle=str(getattr(item, "Achse", "front")),
                size=str(getattr(item, "Dimension", "")),
                load_index=(str(item.Tragfaehigkeit) if getattr(item, "Tragfaehigkeit", None) else None),
                speed_rating=(str(item.Geschwindigkeit) if getattr(item, "Geschwindigkeit", None) else None),
            )
            for item in (getattr(response, "Pneus", None) or [])
        ]

    def fetch_images(self, fz_key: str) -> list[VariantImageData]:
        response = self._call("Bilder", FzKey=fz_key)
        return [
            VariantImageData(
                bild_typ=str(getattr(item, "BildTyp", "")),
                bild_art=str(getattr(item, "BildArt", "")),
                image_key=str(getattr(item, "BildKey", "")),
                sequence=int(getattr(item, "Reihenfolge", 0) or 0),
            )
            for item in (getattr(response, "Bilder", None) or [])
        ]

    def fetch_valuation(
        self, *, fz_key: str, model_year: int, first_registration: dt.date, valuation_date: dt.date, mileage: int
    ) -> ValuationResult:
        response = self._call(
            "Fahrzeuge",
            FzKey=fz_key,
            Bewertung=True,
            ErstzulassungsDatum=first_registration.isoformat(),
            BewertungsDatum=valuation_date.isoformat(),
            Kilometerstand=mileage,
        )
        return ValuationResult(
            provider_value=Decimal(str(getattr(response, "Bewertungswert", "0"))),
            status_code=str(getattr(response, "BewertungsStatus", "ok")),
        )

    def fetch_forecast(self, *, fz_key: str, model_year: int) -> ForecastResult:
        response = self._call("Fahrzeuge", FzKey=fz_key, Forecast=True)
        return ForecastResult(
            residual_value=Decimal(str(getattr(response, "Restwert", "0"))),
            forecast_date=dt.date.fromisoformat(str(getattr(response, "ForecastDatum", utcnow().date().isoformat()))),
        )


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

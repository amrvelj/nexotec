"""The identAPI's valuation of a configured car (KAN-552).

«Bewertung von auto-i-dat holen» is the advisor's deliberate action — never
an automatic call on opening anything. One click asks the Betrieb's own
connection for the car's current value (`vehiclevaluation`) and, once that
answered, for its predefined residual-value table (`residualvalue_table`):
two requests, each its own call-log row under one correlation id, which the
quote keeps as `provider_request_ref`.

The answer is stored as a `ValuationProviderQuote` (Anto, 2026-11-09,
option A) and the valuation created next references it: a valuation is never
edited after creation (ADR-066), and the provider figure on it is the
provider's, never typed. «Wert auto-i-dat» is the trade-in (exchange) price;
purchase and sale prices are kept beside it (Anto, 2026-11-09).

Refusals, each before anything is stored:
- no enabled vehicle-data connection → 409 `no_provider_connection`;
- the connection's `bewertung` option not granted (read strictly: never
  probed is not granted) or refused by the provider at call time (403) →
  409 `not_entitled`;
- a failure, a timeout, an open breaker, or the provider's own «offline
  (maintenance)» state → 422 `provider_unavailable`;
- a connection without its client id or secret → 409 `provider_not_configured`;
- the provider's own «cannot value this car» (its `state`, e.g. too old) →
  422 `provider_cannot_value`, with its code and message as delivered (the
  identAPI words `stateMsg` in the first language asked, German: provider
  text, never translated — ADR-044);
- no FzKey for the car at that provider (a manual configuration) → 422
  `no_catalogue_variant` (from `vehicle.public`);
- no first registration or mileage → 422 `provider_quote_inputs_missing`.

The residual table is best effort: a refusal or failure of that second
request keeps the valuation and records why the table is missing.
"""

import dataclasses
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.uuid7 import uuid7
from app.integration.public import (
    ProviderConfigurationError,
    ProviderGatewayError,
    ProviderNotEntitledError,
    ProviderRequestError,
    call_capability,
    valuation_granted,
)
from app.valuation.models.valuation import ResidualTableState, ValuationProviderQuote
from app.valuation.schemas.valuation import ProviderQuoteRequest
from app.vehicle.public import get_provider_valuation_input

_CALL_LABEL = "valuation"
# `info.state` 1 of `/vehiclevaluation`: «Web service is offline (maintenance)».
_STATE_OFFLINE = 1
_PURPOSE = "provider_valuation"


def _unavailable() -> UnprocessableEntityError:
    return UnprocessableEntityError(
        "The provider's valuation is not available right now.", details={"reason": "provider_unavailable"}
    )


def _not_entitled() -> ConflictError:
    return ConflictError(
        "Valuation is not in this Betrieb's provider contract.", details={"reason": "not_entitled"}
    )


def _cannot_value(*, state: int | None, message: str | None) -> UnprocessableEntityError:
    return UnprocessableEntityError(
        "The provider cannot value this car.",
        details={"reason": "provider_cannot_value", "stateCode": state, "providerMessage": message},
    )


def request_provider_quote(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID | None, data: ProviderQuoteRequest
) -> ValuationProviderQuote:
    car = get_provider_valuation_input(db, tenant_id=tenant_id, configuration_id=data.configuration_id)
    if car is None:
        raise ConflictError(
            "No vehicle-data provider is connected for this Betrieb.", details={"reason": "no_provider_connection"}
        )
    if not valuation_granted(db, connection_id=car.connection.id):
        raise _not_entitled()

    first_registration = data.first_registration or car.first_registration_date
    mileage = data.mileage if data.mileage is not None else car.mileage_km
    missing = [
        field
        for field, value in (("firstRegistration", first_registration), ("mileage", mileage))
        if value is None
    ]
    if missing or first_registration is None or mileage is None:
        raise UnprocessableEntityError(
            "The provider values a car by its first registration and mileage.",
            details={"reason": "provider_quote_inputs_missing", "fields": missing},
        )
    model_year = car.model_year_for(first_registration)
    request_ref = uuid7()
    fz_key, option_keys = car.fz_key, list(car.option_keys)

    try:
        with call_capability(
            db, connection=car.connection, capability=_CALL_LABEL, correlation_id=request_ref,
            actor_id=actor_id, purpose=_PURPOSE,
        ) as adapter:
            valuation = adapter.fetch_vehicle_valuation(
                fz_key=fz_key, model_year=model_year, first_registration=first_registration, mileage_km=mileage,
                option_keys=option_keys,
            )
    except ProviderNotEntitledError:
        raise _not_entitled() from None
    except ProviderRequestError:
        # A 400/422: the provider's coherent refusal of this car's inputs.
        raise _cannot_value(state=None, message=None) from None
    except ProviderConfigurationError:
        # The connection lacks its client id or secret, or there is nobody to
        # enrol: the dealer's to fix in Dealer Administration, not «later».
        raise ConflictError(
            "The provider connection is not fully set up.", details={"reason": "provider_not_configured"}
        ) from None
    except (ProviderGatewayError, KeyError):
        raise _unavailable() from None
    if valuation.state == _STATE_OFFLINE:
        # The provider's own «offline (maintenance)» is an outage, not a verdict on the car.
        raise _unavailable()
    if valuation.state != 0 or valuation.exchange_price is None:
        raise _cannot_value(state=valuation.state, message=valuation.state_message)

    table_state, table_rows = ResidualTableState.PRESENT, []
    try:
        # A block of its own: a failure here is charged to the breaker like
        # any other, and never takes the valuation that already answered.
        with call_capability(
            db, connection=car.connection, capability=_CALL_LABEL, correlation_id=request_ref,
            actor_id=actor_id, purpose=_PURPOSE,
        ) as adapter:
            table = adapter.fetch_residual_table(
                fz_key=fz_key, model_year=model_year, first_registration=first_registration, mileage_km=mileage,
                option_keys=option_keys,
            )
        if table.state != 0:
            table_state = ResidualTableState.DECLINED
        else:
            table_rows = [
                {
                    "durationMonths": row.duration_months,
                    "kmPerYear": row.km_per_year,
                    "totalPercent": str(row.total_percent) if row.total_percent is not None else None,
                    "totalPrice": str(row.total_price) if row.total_price is not None else None,
                }
                for row in table.rows
            ]
    except ProviderNotEntitledError:
        table_state = ResidualTableState.NOT_IN_CONTRACT
    except (ProviderGatewayError, KeyError):
        table_state = ResidualTableState.UNAVAILABLE

    quote = ValuationProviderQuote(
        tenant_id=tenant_id,
        configuration_id=car.configuration_id,
        configuration_label=car.configuration_label,
        configuration_label_refreshed_at=utcnow(),
        fz_key=car.fz_key,
        model_year=model_year,
        first_registration=first_registration,
        mileage=mileage,
        option_keys=list(car.option_keys),
        exchange_price=valuation.exchange_price,
        purchase_price=valuation.purchase_price,
        sale_price=valuation.sale_price,
        vehicle_exchange_price=valuation.vehicle_exchange_price,
        options_exchange_price=valuation.options_exchange_price,
        kilometre_correction_exchange=valuation.kilometre_correction_exchange,
        kilometre_expected=valuation.kilometre_expected,
        exchange_range_from=valuation.exchange_range_from,
        exchange_range_to=valuation.exchange_range_to,
        provider_manual=valuation.manually,
        screen_messages=[dataclasses.asdict(m) for m in valuation.screen_messages],
        print_messages=[dataclasses.asdict(m) for m in valuation.print_messages],
        residual_table_state=table_state,
        residual_table=table_rows,
        provider_request_ref=request_ref,
        fetched_at=utcnow(),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(quote)
    db.flush()
    record_audit_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        entity_type="valuation_provider_quote",
        entity_id=quote.id,
        action="create",
        after={
            "configurationId": str(quote.configuration_id),
            "modelYear": model_year,
            "residualTableState": table_state.value,
            "providerRequestRef": str(request_ref),
        },
    )
    db.commit()
    db.refresh(quote)
    return quote


def get_quote_or_404(db: Session, *, tenant_id: uuid.UUID, quote_id: uuid.UUID) -> ValuationProviderQuote:
    quote = db.get(ValuationProviderQuote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        # 404, never 403 (rule 7).
        raise NotFoundError(f"Provider quote {quote_id} was not found.")
    return quote


def quotes_by_id(
    db: Session, *, tenant_id: uuid.UUID, quote_ids: set[uuid.UUID]
) -> dict[uuid.UUID, ValuationProviderQuote]:
    """The quotes a page of valuations references, in one read."""

    if not quote_ids:
        return {}
    rows = db.scalars(
        select(ValuationProviderQuote).where(
            ValuationProviderQuote.tenant_id == tenant_id, ValuationProviderQuote.id.in_(quote_ids)
        )
    ).all()
    return {row.id: row for row in rows}

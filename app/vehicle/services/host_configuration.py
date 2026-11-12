"""The value a host reads from a configuration (C-F / KAN-10) — kept free
of service imports so `app.vehicle.public` can re-export it without
loading the services (the customer ↔ vehicle import cycle `public.py` once
documented is gone since KAN-634). Produced by
`app.vehicle.services.configuration_host.get_configuration_for_host`.
"""

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from app.core.errors import UnprocessableEntityError
from app.integration.public import IntegrationConnection

# Literals, not `ConfigurationMode.X.value`: the ADR-047 guard
# (tests/architecture/test_adr_047_no_shared_transaction.py) cannot follow a
# public export assigned from an attribute. tests/test_configurator_hosts.py
# pins both to the enum.
BUILD = "build"
RECORD = "record"


class ConfigurationModeNotAllowedError(UnprocessableEntityError):
    """A host was handed a configuration in a mode the PRD v1.4 matrix does
    not allow there (e.g. a `record` configuration on offer Path B)."""

    def __init__(self, *, host: str, mode: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            f"The {host} accepts {' or '.join(allowed)} configurations only, not {mode}.",
            details={"reason": "configuration_mode_not_allowed", "host": host, "mode": mode, "allowed": list(allowed)},
        )


@dataclasses.dataclass(frozen=True)
class ConfigurationPriceLine:
    """One line of the catalogue's price build-up (FR-C-03): a selected
    option, a colour surcharge or a wheels surcharge. Text is the
    provider's, as delivered (ADR-044)."""

    kind: str  # "option" | "exterior_colour" | "interior_colour" | "wheels"
    code: str | None
    label: str
    price: Decimal
    # KAN-595 — an option line's text in each language as the provider
    # delivered it ({"de": …, "fr": …, "it": …, "en": …}; a language it left
    # empty is absent). Empty for a hand-typed option and for the colour and
    # wheels lines, whose text the advisor typed: `label` serves every language.
    labels: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ConfigurationLabel:
    """KAN-534 — a configuration's current label and the Betrieb it belongs
    to: what a host's rule-2 copy (`stock_item.configuration_label`) is
    refreshed from."""

    tenant_id: uuid.UUID
    label: str


@dataclasses.dataclass(frozen=True)
class HostConfiguration:
    id: uuid.UUID
    version: int
    mode: str
    source: str
    catalogue_match_status: str
    label: str
    brand_display_name: str | None
    model_group_name: str | None
    variant_name: str | None
    vin: str | None
    licence_plate: str | None
    first_registration_date: dt.date | None
    mileage_km: int | None
    vehicle_id: uuid.UUID | None
    base_price: Decimal | None
    base_price_year: int | None
    # Empty in `record` mode: on a used car the options are inside the
    # asking price, and itemising them gives the same money away twice
    # (FR-C-04, FR-S-07).
    price_lines: list[ConfigurationPriceLine]
    spec: dict[str, Any]

    def require_mode(self, *, host: str, allowed: tuple[str, ...]) -> None:
        if self.mode not in allowed:
            raise ConfigurationModeNotAllowedError(host=host, mode=self.mode, allowed=allowed)


# -- KAN-552: what the identAPI's valuation needs (services/provider_valuation_input.py)


@dataclasses.dataclass(frozen=True)
class ProviderValuationInput:
    connection: IntegrationConnection
    configuration_id: uuid.UUID
    configuration_label: str | None
    fz_key: str
    model_year_from: int
    model_year_to: int | None
    first_registration_date: dt.date | None
    mileage_km: int | None
    option_keys: list[int]

    def model_year_for(self, first_registration: dt.date) -> int:
        year = max(first_registration.year, self.model_year_from)
        return min(year, self.model_year_to) if self.model_year_to is not None else year


class NoCatalogueVariantError(UnprocessableEntityError):
    def __init__(self) -> None:
        super().__init__(
            "This configuration has no catalogue variant the provider knows, so the provider cannot value it.",
            details={"reason": "no_catalogue_variant"},
        )

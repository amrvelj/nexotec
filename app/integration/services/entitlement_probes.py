"""Per-capability entitlement probing (Configurator C-0 / KAN-38 PR 2b;
restated for the identAPI by KAN-488).

This module holds provider knowledge only — *which* capability maps to
*which* call, and *which* answer means "refused". It never talks to the
database or the gateway; `services/gateway.py::test_connection` owns the
calling discipline (call log, circuit breaker) and
`services/connections.py::record_probed_entitlement` owns the write.

**The identAPI says "not entitled" explicitly**: a 403 on the endpoint that
represents a licence option (`ProviderNotEntitledError`). Each of the six
options is probed by one documented call with auto-i's own documentation
example as input (`adapters/auto_i_dat_ident.py`); 200 (or 404) is granted,
403 is not, and anything else — a refused login, an outage, a 400/422 on our
request — leaves the row exactly as it was. Nothing is inferred: the SOAP
protocol's empty-string answer, which had to be read as "not this Datenname",
left with the SOAP adapter (KAN-550).
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from app.integration.adapters.base import ProviderAdapter
from app.integration.errors import ProviderNotEntitledError


@dataclass(frozen=True)
class CapabilityProbe:
    capability_code: str
    call: Callable[[ProviderAdapter], object]


# KAN-488 — the identAPI's licence options, one documented call each
# (`adapters/auto_i_dat_ident.py`): a 403 is the provider's own "not this
# option" (`ProviderNotEntitledError`), nothing is inferred. `vin` is probed
# too: the test licence has it off (§8), and the row that says so is what
# keeps the VIN rung from ever calling it (ADR-113).
_IDENT_API_PROBES: tuple[CapabilityProbe, ...] = (
    CapabilityProbe("ident_basis", lambda adapter: adapter.probe_ident_basis()),  # type: ignore[attr-defined]
    CapabilityProbe("ausstattung", lambda adapter: adapter.probe_ausstattung()),  # type: ignore[attr-defined]
    CapabilityProbe("bewertung", lambda adapter: adapter.probe_bewertung()),  # type: ignore[attr-defined]
    CapabilityProbe("vin", lambda adapter: adapter.probe_vin()),  # type: ignore[attr-defined]
    CapabilityProbe("pneu", lambda adapter: adapter.probe_pneu()),  # type: ignore[attr-defined]
    CapabilityProbe("energielabel", lambda adapter: adapter.probe_energielabel()),  # type: ignore[attr-defined]
)

# The mock is fully entitled by construction, so probing it writes a
# `granted` row — which is what lets a dev/staging connection show a populated
# entitlement list and lets the granted path run in tests without patching.
# Its basis probe is one brand list of passenger cars (KAN-550: an identAPI
# operation, as every mock method is).
_MOCK_IDENT_BASIS = CapabilityProbe("ident_basis", lambda adapter: adapter.list_catalogue_brands(vehicle_kind_code="01"))
# KAN-552: the demo valuation is behind the `bewertung` row like the real one
# (read strictly), so the mock probes it too — one residual table of a demo
# variant. `vin` stays unprobed: the demo shows what the test licence shows,
# «VIN-Abfrage nicht im Vertrag».
_MOCK_BEWERTUNG = CapabilityProbe(
    "bewertung",
    lambda adapter: adapter.fetch_residual_table(
        fz_key="FZ100001", model_year=2020, first_registration=dt.date(2020, 1, 1), mileage_km=30000, option_keys=[]
    ),
)

_PROBES_BY_PROVIDER: dict[str, tuple[CapabilityProbe, ...]] = {
    "auto_i_dat": _IDENT_API_PROBES,
    "auto_i_dat_mock": (_MOCK_IDENT_BASIS, _MOCK_BEWERTUNG),
}

# What `gateway.test_connection` calls first to prove the credentials: for the
# identAPI the token request followed by `GET /caption/code?lang=de`, which
# every licence may read (KAN-488); for every other vehicle-data adapter (the
# demo provider) the captions in German through the Protocol (KAN-550: the
# SOAP `System` watermark is gone).
_ACCESS_CHECKS: dict[str, Callable[[ProviderAdapter], object]] = {
    "auto_i_dat": lambda adapter: adapter.fetch_code_captions("de"),  # type: ignore[attr-defined]
}


def access_check(provider_code: str) -> Callable[[ProviderAdapter], object]:
    return _ACCESS_CHECKS.get(provider_code, lambda adapter: adapter.fetch_captions("de"))


def probes_for(provider_code: str) -> tuple[CapabilityProbe, ...]:
    return _PROBES_BY_PROVIDER.get(provider_code, ())


def run_probe(probe: CapabilityProbe, adapter: ProviderAdapter) -> bool:
    """`True` = the provider answered (data or "no data"), `False` = it
    refused the capability. Only a refusal of the capability reads as not
    granted: the identAPI's 403 (`ProviderNotEntitledError`, KAN-488).
    Everything else propagates — a refused login
    (`errors.ProviderRejectedError`: a 401 twice, a bad secret; the caller
    stops probing on it), maintenance, a transport failure, a 400/422
    (`ProviderRequestError`: our request, not the licence) — because none of
    them says anything about what the account is entitled to, and none may
    switch off an option (KAN-488 review).

    Only meaningful when the access check was accepted for the same
    connection just before; see the module docstring for why that ordering is
    what makes a refusal attributable to the capability.
    """

    try:
        probe.call(adapter)
    except ProviderNotEntitledError:
        return False
    return True

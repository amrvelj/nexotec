"""Per-capability entitlement probing (Configurator C-0 / KAN-38 PR 2b).

This module holds provider knowledge only — *which* capability maps to
*which* call, and *which* answer means "refused". It never talks to the
database or the gateway; `services/gateway.py::test_connection` owns the
calling discipline (call log, circuit breaker) and
`services/connections.py::record_probed_entitlement` owns the write.

**The protocol has no "not entitled" signal.** *Webservice Fahrzeuge* p4
documents ``Status`` 0 / 1 / 2 and nothing else; an empty string means
"invalid Benutzername / Passwort / Sprache / Datenname". Entitlement is
therefore *inferred*, never read, and the inference below cannot be
verified until a staging account exists (KAN-38 exit criterion 1 has the
same blocker) — `scripts/verify_auto_i_dat.py` prints the probe outcome so
it can be checked against the account sheet the day one does.

**Why `fahrzeuge` is the one capability probed.** The probe runs only after
`System` has just accepted the same connection, which rules out three of
p4's four causes (Benutzername, Passwort, Sprache). A criteria-free
`FahrzeugArten` that then comes back as an empty string can only be the
fourth: the Datenname itself. With no ``Suchwerte`` there is also nothing
that could have been malformed.

The other sheet capabilities are not probed: their Datennamen need real
business input (a vehicle key, a plate, a Typenschein), and an invented one
cannot tell "no such vehicle" (``Status`` 2) from "not entitled". The
asymmetry decides it — a wrong "granted" is the optimistic default this
registry already applies everywhere, while a wrong "not granted" would
switch off a capability the dealer paid for.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.integration.adapters.auto_i_dat_parse import ProviderRejectedError
from app.integration.adapters.base import ProviderAdapter


@dataclass(frozen=True)
class CapabilityProbe:
    capability_code: str
    call: Callable[[ProviderAdapter], object]


_FAHRZEUGE = CapabilityProbe("fahrzeuge", lambda adapter: adapter.list_vehicle_kinds())

# The mock is fully entitled by construction, so probing it writes a
# `granted` row — which is what lets a dev/staging connection show a populated
# entitlement list and lets the granted path run in tests without patching.
_PROBES_BY_PROVIDER: dict[str, tuple[CapabilityProbe, ...]] = {
    "auto_i_dat": (_FAHRZEUGE,),
    "auto_i_dat_mock": (_FAHRZEUGE,),
}


def probes_for(provider_code: str) -> tuple[CapabilityProbe, ...]:
    return _PROBES_BY_PROVIDER.get(provider_code, ())


def run_probe(probe: CapabilityProbe, adapter: ProviderAdapter) -> bool:
    """`True` = the provider answered (data or "no data"), `False` = it
    refused. Only `ProviderRejectedError` means refused — maintenance and
    transport failures propagate, because an outage says nothing about what
    the account is entitled to.

    Only meaningful when `System` was accepted for the same connection just
    before; see the module docstring for why that ordering is what makes a
    rejection attributable to the Datenname.
    """

    try:
        probe.call(adapter)
    except ProviderRejectedError:
        return False
    return True

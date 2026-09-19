"""Executable verification for the auto-i-dat adapter (Configurator C-0 /
KAN-38). This is the check that closes **exit criterion 1** — "a
successful ``KontrollschildInfo`` round trip against a real account, not a
mock test passing" — once the staging account (KAN-38 blocker) exists.

It runs each **implemented** Datenname through the *real* SOAP adapter
against a configured ``auto_i_dat`` connection, and diffs the parsed shape
against ``MockAutoIDatAdapter``'s output for the same call. Per-Datenname
it prints ``PASS`` / ``FAIL`` / ``SKIP``.

PR 1 implemented the seven transport-migrated Datennamen; **PR 2 adds the
fourteen new ones below** (``KontrollschildInfo`` first — it is the named
exit criterion). Criterion 1 itself stays open regardless: it needs a real
account, which this script can exercise but not conjure.

Usage
-----
    DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... PYTHONPATH=. \\
      python scripts/verify_auto_i_dat.py --connection-id <uuid> \\
        [--fz-key 141695] [--werkscode 191B51] [--typ-sch-nr 1MD448] [--model-year 2012] \\
        [--plate ZH123456] [--fz-art 01] [--opt-key 100369] [--code-group 010]

With no ``--connection-id`` every Datenname reports ``SKIP (no
connection)`` and the script exits 0 — so it is safe to wire into CI
before the account lands.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from dataclasses import fields, is_dataclass
from typing import Any

# The seven Datennamen migrated onto the real `Suchen` transport in PR 1.
# (datenname, adapter-method-name, kwargs-builder)
_IMPLEMENTED: list[tuple[str, str, str]] = [
    ("Fahrzeuge", "fetch_vehicle_master_data", "fz_key"),
    ("FzKeyChanged", "list_changed_keys", "since"),
    ("System", "get_system_watermark", "none"),
    ("Optionen", "fetch_options", "fz_key+model_year"),
    ("OptionenFarben", "fetch_colours", "werkscode"),
    ("PneuDimTS", "fetch_tyre_specs", "typ_sch_nr"),
    ("Bilder", "fetch_images", "fz_key"),
    # PR 2 — the fourteen new Datennamen. KontrollschildInfo first: it is
    # exit criterion 1, which stays open (needs the real account) even
    # though the code is now in place.
    ("KontrollschildInfo", "lookup_plate", "plate"),  # <-- exit criterion 1
    ("Codes", "fetch_codes", "none"),
    ("FahrzeugArten", "list_vehicle_kinds", "none"),
    ("Marken", "list_brands", "fz_art"),
    ("ModellGruppen", "list_model_groups", "fz_art"),
    ("ModellGruppenKurz", "list_model_groups_short", "fz_art"),
    ("FzgWerteGruppiert", "fetch_grouped_values", "fz_art+gruppiert"),
    ("Typenscheine", "fetch_type_approvals", "fz_key"),
    ("FahrzeugeMatch", "find_best_match", "typ_sch_nr+neupreis"),
    ("FahrzeugePreise", "fetch_vehicle_prices", "fz_key+model_year"),
    ("FzgDatenTS", "fetch_type_approval_data", "typ_sch_nr"),
    ("OptionenPack", "fetch_option_package_contents", "opt_key"),
    ("OptionenAusschluss", "fetch_option_exclusions", "fz_key+model_year+opt_key"),
    ("OptionenZusatz", "fetch_option_conditions", "fz_key+model_year+opt_key"),
]


def _shape(value: Any) -> Any:
    """A structural fingerprint: dataclass field names + types, recursively
    for lists. Used to diff the real response against the mock's.
    """

    if isinstance(value, list):
        return ["list", _shape(value[0]) if value else "empty"]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: type(getattr(value, f.name)).__name__ for f in fields(value)}
    return type(value).__name__


def _call(adapter: Any, method: str, selector: str, args: argparse.Namespace) -> Any:
    fn = getattr(adapter, method)
    if selector == "none":
        return fn()
    if selector == "fz_key":
        return fn(args.fz_key)
    if selector == "since":
        return fn(since=dt.datetime.now(dt.UTC).date() - dt.timedelta(days=30))
    if selector == "fz_key+model_year":
        return fn(args.fz_key, model_year=args.model_year)
    if selector == "werkscode":
        return fn(werkscode=args.werkscode)
    if selector == "typ_sch_nr":
        # `fetch_tyre_specs`'s own kwarg name predates PR 2 and stays as
        # `type_approval_number`; the fourteen new methods below all spell
        # it `typ_sch_nr`, matching the spec's own field name more
        # directly — both selectors read from the same `--typ-sch-nr` arg.
        if method == "fetch_tyre_specs":
            return fn(type_approval_number=args.typ_sch_nr)
        return fn(args.typ_sch_nr)
    if selector == "plate":
        return fn(args.plate, fz_art=args.fz_art)
    if selector == "fz_art":
        return fn(fz_art=args.fz_art)
    if selector == "fz_art+gruppiert":
        return fn(fz_art=args.fz_art, gruppiert=args.gruppiert)
    if selector == "typ_sch_nr+neupreis":
        return fn(typ_sch_nr=args.typ_sch_nr, neupreis=args.neupreis)
    if selector == "opt_key":
        return fn(args.opt_key)
    if selector == "fz_key+model_year+opt_key":
        return fn(args.fz_key, year=args.model_year, opt_key=args.opt_key)
    raise ValueError(f"unknown selector {selector!r}")


def _report_entitlement_probes(adapter: Any) -> None:
    """Informational only — never affects the exit code. The entitlement
    probe (KAN-38 PR 2b) *infers* "not entitled" from an empty string,
    because the protocol has no such signal; this prints what it concluded
    so a human can hold it against the account sheet. Until that has been
    done once against a real account, the inference is unverified.
    """

    from app.integration.services import entitlement_probes

    print("-" * 72)
    print("entitlement inference — compare with the account sheet (unverified until this has been done once):")
    for probe in entitlement_probes.probes_for("auto_i_dat"):
        try:
            verdict = "granted" if entitlement_probes.run_probe(probe, adapter) else "REFUSED"
        except Exception as exc:  # noqa: BLE001 - a verification script reports, never crashes
            print(f"{probe.capability_code:<18} {'ERROR':<8} {type(exc).__name__}: {exc}")
        else:
            print(f"{probe.capability_code:<18} {verdict:<8}")
    print(f"not probed (see entitlement_probes.UNPROBEABLE): {', '.join(sorted(entitlement_probes.UNPROBEABLE))}")


def _run(args: argparse.Namespace) -> int:
    from app.integration.adapters.auto_i_dat_mock import MockAutoIDatAdapter

    mock = MockAutoIDatAdapter()
    real = None
    if args.connection_id is not None:
        from app.db import SessionLocal
        from app.integration.adapters.auto_i_dat_soap import _build_real_adapter
        from app.integration.services.connections import get_connection_or_404

        db = SessionLocal()
        connection = get_connection_or_404(db, tenant_id=None, connection_id=args.connection_id)
        real = _build_real_adapter(db, connection, actor_id=None, purpose="verify_auto_i_dat")

    failures = 0
    print(f"auto-i-dat adapter verification — {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}")
    print(f"{'Datenname':<18} {'result':<8} detail")
    print("-" * 72)

    for datenname, method, selector in _IMPLEMENTED:
        if real is None:
            print(f"{datenname:<18} {'SKIP':<8} no connection (pass --connection-id to run for real)")
            continue
        try:
            real_out = _call(real, method, selector, args)
        except Exception as exc:  # noqa: BLE001 - a verification script reports, never crashes
            failures += 1
            print(f"{datenname:<18} {'FAIL':<8} {type(exc).__name__}: {exc}")
            continue
        try:
            mock_out = _call(mock, method, selector, args)
        except Exception:  # noqa: BLE001
            mock_out = None
        real_shape, mock_shape = _shape(real_out), _shape(mock_out) if mock_out is not None else None
        if mock_shape is not None and real_shape != mock_shape:
            failures += 1
            print(f"{datenname:<18} {'FAIL':<8} shape drift  real={real_shape}  mock={mock_shape}")
        else:
            print(f"{datenname:<18} {'PASS':<8} {real_shape}")

    if real is not None:
        _report_entitlement_probes(real)

    print("-" * 72)
    if real is None:
        print("SKIPPED — no --connection-id. Exit criterion 1 is still open (KAN-38 blocker: no staging account).")
        return 0
    if failures:
        print(f"{failures} FAILURE(S). Exit criterion 1 is NOT met.")
        return 1
    print("All 21 implemented Datennamen round-tripped against the real account. Exit criterion 1 is met.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connection-id", type=uuid.UUID, default=None, help="a configured auto_i_dat IntegrationConnection")
    parser.add_argument("--fz-key", default="000000", help="an FzKey known to the account")
    parser.add_argument("--werkscode", default="000000", help="a Werkscode known to the account")
    parser.add_argument("--typ-sch-nr", default="000000", help="a Typenschein number known to the account")
    parser.add_argument(
        "--model-year", type=int, default=dt.datetime.now(dt.UTC).year, help="model year for Optionen"
    )
    parser.add_argument("--plate", default="ZZ000000", help="a Kontrollschild known to the account")
    parser.add_argument("--fz-art", default="01", help="FzArt for Marken/ModellGruppen(Kurz)/FzgWerteGruppiert")
    parser.add_argument("--gruppiert", default="Aufbau", help="dimension for FzgWerteGruppiert")
    parser.add_argument("--neupreis", type=int, default=20000, help="Neupreis for FahrzeugeMatch")
    parser.add_argument("--opt-key", type=int, default=100000, help="an OptKey known to the account")
    args = parser.parse_args()
    sys.exit(_run(args))


if __name__ == "__main__":
    main()

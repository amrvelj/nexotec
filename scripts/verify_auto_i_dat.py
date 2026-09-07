"""Executable verification for the auto-i-dat adapter (Configurator C-0 /
KAN-38). This is the check that closes **exit criterion 1** — "a
successful ``KontrollschildInfo`` round trip against a real account, not a
mock test passing" — once the staging account (KAN-38 blocker) exists.

It runs each **implemented** Datenname through the *real* SOAP adapter
against a configured ``auto_i_dat`` connection, and diffs the parsed shape
against ``MockAutoIDatAdapter``'s output for the same call. Per-Datenname
it prints ``PASS`` / ``FAIL`` / ``SKIP``.

PR 1 implements the seven transport-migrated Datennamen. The fourteen new
ones (PR 2) are listed as ``TODO`` — ``KontrollschildInfo`` first, because
it is the named exit criterion.

Usage
-----
    DMS_DATABASE_URL=... DMS_TAX_ID_ENCRYPTION_KEY=... PYTHONPATH=. \\
      python scripts/verify_auto_i_dat.py --connection-id <uuid> \\
        [--fz-key 141695] [--werkscode 191B51] [--typ-sch-nr 1MD448] [--model-year 2012]

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
]

# PR 2 — added to this script as they are implemented. KontrollschildInfo
# first: it is exit criterion 1.
_TODO_PR2 = [
    "KontrollschildInfo",  # <-- exit criterion 1
    "Codes",
    "FahrzeugArten",
    "Marken",
    "ModellGruppen",
    "ModellGruppenKurz",
    "FzgWerteGruppiert",
    "Typenscheine",
    "FahrzeugeMatch",
    "FahrzeugePreise",
    "FzgDatenTS",
    "OptionenPack",
    "OptionenAusschluss",
    "OptionenZusatz",
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
        return fn(type_approval_number=args.typ_sch_nr)
    raise ValueError(f"unknown selector {selector!r}")


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

    print("-" * 72)
    for datenname in _TODO_PR2:
        marker = "  <-- exit criterion 1" if datenname == "KontrollschildInfo" else ""
        print(f"{datenname:<18} {'TODO':<8} PR 2{marker}")

    print("-" * 72)
    if real is None:
        print("SKIPPED — no --connection-id. Exit criterion 1 is still open (KAN-38 blocker: no staging account).")
        return 0
    if failures:
        print(f"{failures} FAILURE(S). Exit criterion 1 is NOT met.")
        return 1
    print("All implemented Datennamen round-tripped. Add PR 2's fourteen, then criterion 1 is met.")
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
    args = parser.parse_args()
    sys.exit(_run(args))


if __name__ == "__main__":
    main()

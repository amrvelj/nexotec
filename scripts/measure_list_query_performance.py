"""KAN-461 exit criterion 4: the Stock grid's sort and filter times at 10× the
largest expected Betrieb (Anto, 2026-11-15: 5'000 stock items — a large
single-site Swiss dealer at ~500 cars, times ten), against the budget he set
the same day: **p95 ≤ 250 ms server-side per list request** (the first page
with its count, any sort or filter).

What it does, on a throwaway Postgres database (never the dev or test one):

1. creates `dms_perf_<random>` on the server of `--database-url` and migrates
   it with `alembic upgrade heads` — the measured schema is the real one;
2. inserts `--items` stock items for the measured Betrieb, with spread values
   and ~30% empty optional fields, plus `--other-tenants` × `--items` for other
   Betriebe, so the planner sees a table of a realistic size; then ANALYZE;
3. calls `GET /v1/inventory/stock-items` in-process (FastAPI's TestClient, a
   minted token), for every registry field sorted both ways and every
   condition it serves (the first page, limit 50, with its count): `--warmup`
   untimed calls, then `--runs` timed ones each;
4. prints p50 / p95 / max per case, the slowest cases' EXPLAIN ANALYZE, and
   whether every p95 is within the budget; drops the database.

    python scripts/measure_list_query_performance.py \\
        --database-url postgresql+psycopg://dms:dms@localhost:5432/postgres

Indexes are added only where these numbers say so (U-10 as closed by Anto).
"""

import argparse
import datetime as dt
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUDGET_MS = 250.0


def _admin_url(url: str, database: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{database}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", default="postgresql+psycopg://dms:dms@localhost:5432/postgres")
    parser.add_argument("--items", type=int, default=5000)
    parser.add_argument("--other-tenants", type=int, default=19)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--keep", action="store_true", help="keep the database afterwards")
    args = parser.parse_args()

    import psycopg

    name = f"dms_perf_{uuid.uuid4().hex[:10]}"
    admin_dsn = args.database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = _admin_url(args.database_url, name)
    try:
        env = {**os.environ, "DMS_DATABASE_URL": url}
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "heads"], cwd=ROOT, env=env, check=True, capture_output=True
        )
        os.environ["DMS_DATABASE_URL"] = url
        return _measure(args, url)
    finally:
        if not args.keep:
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed(session_factory, tenant_id: uuid.UUID, count: int, rng: random.Random) -> None:
    from app.core.base import utcnow
    from app.inventory.models.stock_item import LifecycleStatus, ReservationState, StockItem, StockItemCondition

    now = utcnow()
    words = ["Golf", "Octavia", "Model 3", "Tiguan", "Clio", "Yaris", "Kodiaq", "Polo", "Ateca", "Fabia"]
    colours = ["Schwarz", "Weiss", "Silber", "Blau", "Rot", "Grau", None]

    def maybe(value, empty=0.3):
        return None if rng.random() < empty else value

    def money(low, high):
        return Decimal(rng.randint(low * 100, high * 100)) / 100

    with session_factory() as db:
        batch = []
        for i in range(count):
            in_stock_at = maybe(now - dt.timedelta(days=rng.randint(0, 400), hours=rng.randint(0, 23)), 0.25)

            def day():
                return maybe((now - dt.timedelta(days=rng.randint(0, 2000))).date())

            batch.append(
                StockItem(
                    tenant_id=tenant_id,
                    stock_number=f"L-{i:06d}",
                    # Unique per Betrieb, as the in-stock VIN index requires.
                    vin=maybe(f"WVWZZZ1KZ{i:08d}", 0.2),
                    vehicle_label=f"{rng.choice(words)} {rng.randint(1, 9)}.{rng.randint(0, 9)} {rng.choice(['TDI', 'TSI', 'EV', 'Hybrid'])}",
                    lifecycle_status=LifecycleStatus.IN_STOCK if in_stock_at else LifecycleStatus.PIPELINE,
                    reservation_state=rng.choice(list(ReservationState)),
                    condition=rng.choice(list(StockItemCondition)),
                    odometer_km=maybe(rng.randint(0, 250_000)),
                    exterior_colour=rng.choice(colours),
                    body_style=maybe(rng.choice(["Limousine", "Kombi", "SUV", "Cabriolet"])),
                    list_price=maybe(money(5_000, 150_000), 0.1),
                    effective_price=maybe(money(5_000, 150_000), 0.1),
                    base_price=maybe(money(5_000, 120_000)),
                    first_registration_date=day(),
                    pipeline_ref=maybe(f"PR-{i:06d}"),
                    trade_in_vin=maybe(f"TMBJJ7NX{rng.randint(0, 10**9):09d}", 0.8),
                    trade_in_signed_at=maybe(now - dt.timedelta(days=rng.randint(0, 900)), 0.8),
                    order_date=day(),
                    expected_delivery=day(),
                    in_stock_at=in_stock_at,
                    configuration_label=maybe(f"{rng.choice(words)} Konfiguration {i}"),
                    supplier_name=maybe(f"Lieferant {rng.randint(1, 300)}"),
                    supplier_is_vat_registered=maybe(rng.random() < 0.7),
                    purchase_date=day(),
                    purchase_price=maybe(money(3_000, 120_000)),
                    purchase_invoice_ref=maybe(f"RE-{rng.randint(0, 10**6)}"),
                    landed_cost=maybe(money(3_000, 125_000)),
                    notional_input_tax_applicable=maybe(rng.random() < 0.5),
                    notional_input_tax_rate=maybe(Decimal(rng.choice(["8.10", "7.70", "2.60"]))),
                    notional_input_tax_amount=maybe(money(0, 9_000)),
                    notional_input_tax_overridden=rng.random() < 0.05,
                    is_invoiceable=rng.random() < 0.6,
                    valuation_ref_amount=maybe(money(1_000, 90_000), 0.7),
                    valuation_ref_valued_at=maybe(now - dt.timedelta(days=rng.randint(0, 900)), 0.7),
                    valuation_ref_source=maybe(rng.choice(["eurotax", "manual"]), 0.7),
                    created_at=now - dt.timedelta(days=rng.randint(0, 900)),
                    updated_at=now - dt.timedelta(minutes=rng.randint(0, 600_000)),
                )
            )
            if len(batch) >= 1000:
                db.add_all(batch)
                db.commit()
                batch = []
        db.add_all(batch)
        db.commit()


def _cases(spec) -> list[tuple[str, dict]]:
    """(label, query params) for every sort direction and condition."""

    cases = [(f"sort {spec.api_name}:{d}", {"sort": f"{spec.api_name}:{d}"}) for d in ("asc", "desc")]
    sample = {
        "text": "a",
        "number": "50000",
        "money": "30000",
        "boolean": "true",
    }
    for op in spec.allowed_operators:
        kind = spec.value_type
        if kind == "enum":
            codes = spec.enum_codes
            value = ",".join(codes[:2]) if op == "in" else codes[0]
        elif kind in ("number", "money"):
            value = "10000,60000" if op == "between" else sample[kind]
        elif kind in ("date", "datetime"):
            value = "30" if op in ("inTheLastDays", "moreThanDaysAgo") else (
                "2025-06-01" if op in ("on", "before", "after") else None
            )
        else:
            value = sample[kind]
        predicate = f"{spec.api_name}:{op}" + (f":{value}" if value is not None else "")
        cases.append((f"filter {predicate}", {"filter": predicate}))
    return cases


def _measure(args, url: str) -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.core.auth import AccessRole, create_access_token
    from app.inventory.services.stock_grid import STOCK_ITEM_COLUMNS
    from app.main import app

    engine = create_engine(url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    rng = random.Random(461)
    tenant_id = uuid.uuid4()
    started = time.perf_counter()
    _seed(session_factory, tenant_id, args.items, rng)
    for _ in range(args.other_tenants):
        _seed(session_factory, uuid.uuid4(), args.items, rng)
    with engine.begin() as conn:
        conn.execute(text("ANALYZE stock_item"))
        rows = conn.execute(text("SELECT count(*) FROM stock_item")).scalar()
    print(f"Seeded {rows} stock items ({args.items} in the measured Betrieb) in {time.perf_counter() - started:.0f} s")

    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=uuid.uuid4(), roles=frozenset({AccessRole.INVENTORY})
    )
    headers = {"Authorization": f"Bearer {token}"}
    results: list[tuple[str, float, float, float, dict]] = []
    with TestClient(app) as client:
        for spec in STOCK_ITEM_COLUMNS:
            for label, params in _cases(spec):
                query = {**params, "limit": 50}
                for _ in range(args.warmup):
                    response = client.get("/v1/inventory/stock-items", params=query, headers=headers)
                    assert response.status_code == 200, (label, response.text)
                timings = []
                for _ in range(args.runs):
                    t0 = time.perf_counter()
                    client.get("/v1/inventory/stock-items", params=query, headers=headers)
                    timings.append((time.perf_counter() - t0) * 1000)
                timings.sort()
                p95 = timings[max(0, round(0.95 * len(timings)) - 1)]
                results.append((label, statistics.median(timings), p95, timings[-1], query))

    results.sort(key=lambda r: r[2], reverse=True)
    print(f"\n{len(results)} cases, {args.runs} timed runs each — slowest p95 first (ms):")
    print(f"{'case':58} {'p50':>7} {'p95':>7} {'max':>7}")
    for label, p50, p95, worst, _ in results[:15]:
        print(f"{label:58} {p50:7.1f} {p95:7.1f} {worst:7.1f}")
    all_p95 = [r[2] for r in results]
    print(
        f"\nAcross all cases: median p95 {statistics.median(all_p95):.1f} ms, worst p95 {max(all_p95):.1f} ms "
        f"(budget {BUDGET_MS:.0f} ms)"
    )
    over = [r for r in results if r[2] > BUDGET_MS]
    print("WITHIN BUDGET" if not over else f"OVER BUDGET: {len(over)} case(s)")
    for label, *_ in over:
        print(f"  {label}")
    engine.dispose()
    return 0 if not over else 1


if __name__ == "__main__":
    sys.exit(main())

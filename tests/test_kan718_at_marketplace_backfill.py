"""KAN-718: inventory migration `b7180a3c9d14` adds
`stock_item_publishing.at_marketplace` and backfills it, run here for real on
seeded rows (`migrations-scripts.md`: a backfill is proven by a run and a
query).

- a published row whose last transmission reached the marketplace is held;
- an unpublished `transmitted` row is held unless a later feed of its
  channel was delivered (a published `transmitted` row of the same Betrieb
  and channel attempted after the unpublish) — that feed omitted it;
- FAILED and PENDING rows are not held (the second case before the
  migration is Anto's question, named in the migration).

Tests build the schema with ``create_all``, so the migration is loaded by
path and its own `downgrade()` / `upgrade()` run against the test schema
(the `test_kan540_*` pattern).
"""

import datetime as dt
import importlib.util
import pathlib
import uuid

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.inventory.services import marketplace_transmission
from app.inventory.services.publishing import publish
from tests.postgres_only import POSTGRES_SQL
from tests.test_inventory_marketplace_transmission import (
    FakeMarketplaceAdapter,
    _make_connection,
    _make_provider,
    _make_ready_item,
    _register_fake,
)
from tests.test_kan218_offboarding_withdraws_listings_and_connections import _AS24
from tests.test_sales_manual_configuration_link import _dealership

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "inventory"


def _load():
    (path,) = sorted(_VERSIONS.glob("b7180a3c9d14_*.py"))
    spec = importlib.util.spec_from_file_location("kan718_at_marketplace", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load()


def _run(engine, step) -> None:
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        step()


def _seed(db_session, monkeypatch, vins: list[str], provider=None) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """One Betrieb, one AutoScout24 connection, its listings published and delivered."""

    _register_fake(monkeypatch, FakeMarketplaceAdapter())
    dealership = _dealership(db_session)
    _make_connection(db_session, provider or _make_provider(db_session), tenant_id=dealership.id)
    items = [_make_ready_item(db_session, dealership.id, vin=vin) for vin in vins]
    for item in items:
        publish(db_session, item=item, channel=_AS24, actor_id=uuid.uuid4())
    marketplace_transmission.assemble_and_transmit(db_session, tenant_id=dealership.id, channel=_AS24)
    return dealership.id, [item.id for item in items]


def _set(engine, stock_item_id, *, state: str, status: str, attempted: dt.datetime | None, updated: dt.datetime):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE stock_item_publishing SET state = :state, transmission_status = :status,"
                " last_attempted_at = :attempted, updated_at = :updated WHERE stock_item_id = :item"
            ),
            {"state": state, "status": status, "attempted": attempted, "updated": updated, "item": stock_item_id},
        )


def _held(engine) -> dict[uuid.UUID, bool]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT stock_item_id, at_marketplace FROM stock_item_publishing")).all()
    return {uuid.UUID(str(item)): held for item, held in rows}


@POSTGRES_SQL
def test_the_backfill_holds_what_the_last_delivered_feed_may_still_hold(db_session, engine, monkeypatch):
    base = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    _tenant, (live, gone, unpublished_after, failed, pending) = _seed(
        db_session, monkeypatch, [f"WVWZZZ1KZAW71801{n}" for n in range(5)]
    )
    # The channel's last delivered feed went out at base + 2h; `live` was in it.
    _set(engine, live, state="published", status="transmitted", attempted=base + dt.timedelta(hours=2), updated=base)
    # Unpublished at base + 1h, before that feed: the feed omitted it.
    _set(engine, gone, state="not_published", status="transmitted", attempted=base, updated=base + dt.timedelta(hours=1))
    # Unpublished at base + 3h, after the last delivered feed: still held.
    _set(
        engine, unpublished_after, state="not_published", status="transmitted", attempted=base,
        updated=base + dt.timedelta(hours=3),
    )
    _set(engine, failed, state="published", status="failed", attempted=base, updated=base)
    _set(engine, pending, state="not_published", status="pending", attempted=None, updated=base)
    db_session.expire_all()

    _run(engine, _MIGRATION.downgrade)
    _run(engine, _MIGRATION.upgrade)

    assert _held(engine) == {live: True, gone: False, unpublished_after: True, failed: False, pending: False}


@POSTGRES_SQL
def test_an_unpublish_with_no_listing_left_on_its_channel_stays_held(db_session, engine, monkeypatch):
    """The channel's last listing was unpublished: no later delivered feed
    of a published row tells whether its empty feed arrived — held."""

    base = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    _tenant, (only,) = _seed(db_session, monkeypatch, ["WVWZZZ1KZAW718020"])
    _set(engine, only, state="not_published", status="transmitted", attempted=base, updated=base + dt.timedelta(hours=1))
    db_session.expire_all()

    _run(engine, _MIGRATION.downgrade)
    _run(engine, _MIGRATION.upgrade)

    assert _held(engine) == {only: True}


@POSTGRES_SQL
def test_another_betriebs_later_feed_clears_nothing(db_session, engine, monkeypatch):
    base = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    provider = _make_provider(db_session)
    _one, (unpublished,) = _seed(db_session, monkeypatch, ["WVWZZZ1KZAW718030"], provider)
    _two, (elsewhere,) = _seed(db_session, monkeypatch, ["WVWZZZ1KZAW718031"], provider)
    _set(engine, unpublished, state="not_published", status="transmitted", attempted=base, updated=base)
    _set(engine, elsewhere, state="published", status="transmitted", attempted=base + dt.timedelta(hours=5), updated=base)
    db_session.expire_all()

    _run(engine, _MIGRATION.downgrade)
    _run(engine, _MIGRATION.upgrade)

    assert _held(engine) == {unpublished: True, elsewhere: True}

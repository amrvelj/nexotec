"""KAN-218 — the export is complete (FR-A-44: "every record the Betrieb
owns"), and stays complete.

1. Every mapped table is declared by exactly one context's exporter: in the
   export, or left out with a reason (a secret, a cache, Nexotec's reference
   data, the plate register ADR-039 never lists). A new table fails here until
   its context decides — in particular every table with a `tenant_id` or
   `dealership_id` column.
2. Against a seeded Betrieb, every table in the export holds exactly the rows
   the Betrieb's scope selects in the database, and a second Betrieb's rows
   never appear.

The test prints the declaration (`-s`), the output the PR description cites.
"""

import datetime as dt
import uuid
from collections import Counter
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

import app.model_registry  # noqa: F401 - every mapped table
from app.core.export import exporters, read_rows
from app.db import Base
from app.export_registry import STUB_CONTEXTS
from app.platform.models.dealership import Dealership
from app.platform.services import data_export
from tests.data_export_support import (
    betrieb_with_manager,
    download,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    idem,
    produce,
    request_export,
    rows_in,
)
from tests.membership_support import grant

_TENANT_COLUMNS = ("tenant_id", "dealership_id")


def _declarations() -> dict[str, list[tuple[str, str]]]:
    """table -> [(context, "exported" | "excluded: <reason>")]"""

    declared: dict[str, list[tuple[str, str]]] = {}
    for exporter in exporters():
        for spec in exporter.exported_tables():
            verdict = "exported" if spec.in_archive else f"excluded: {spec.exclusion_reason}"
            declared.setdefault(spec.name, []).append((exporter.context, verdict))
        for table, reason in exporter.excluded_tables().items():
            assert reason and reason.strip(), f"{exporter.context}.{table} is excluded without a reason"
            declared.setdefault(table, []).append((exporter.context, f"excluded: {reason}"))
    return declared


def test_every_mapped_table_is_exported_or_excluded_with_a_reason_exactly_once(capsys):
    declared = _declarations()
    tables = Base.metadata.tables

    undeclared = sorted(name for name in tables if name not in declared)
    twice = sorted(name for name, entries in declared.items() if len(entries) > 1)
    unknown = sorted(name for name in declared if name not in tables)

    with capsys.disabled():
        print("\nKAN-218 export completeness:")
        for name in sorted(declared):
            context, verdict = declared[name][0]
            tenant = "tenant" if any(column in tables[name].c for column in _TENANT_COLUMNS) else "      "
            print(f"  {tenant} {context:12} {name:45} {verdict}")
    assert undeclared == [], f"tables no exporter declares: {undeclared}"
    assert twice == [], f"tables declared more than once: {twice}"
    assert unknown == [], f"declarations naming no mapped table: {unknown}"


def test_every_table_with_a_tenant_column_is_declared():
    """The exit criterion's own wording: every table with a tenant_id or
    dealership_id appears in the export or is listed excluded with a reason."""

    declared = _declarations()
    tenant_tables = [name for name, table in Base.metadata.tables.items() if any(c in table.c for c in _TENANT_COLUMNS)]
    assert len(tenant_tables) > 60  # the guard reaches the schema
    assert [name for name in tenant_tables if name not in declared] == []


def test_every_context_is_in_the_registry():
    contexts = {exporter.context for exporter in exporters()}
    assert contexts == {
        "core",
        "platform",
        "integration",
        "vehicle",
        "customer",
        "valuation",
        "inventory",
        "sales",
        *STUB_CONTEXTS,
    }


def _seed(client, engine, dealership_id, headers, manager):
    """A Betrieb with data in each built context: users and a membership, a
    customer with contact channels, a stock item, an offer with a document,
    a valuation, a vehicle configuration and an integration connection."""

    from sqlalchemy import select

    from app.integration.models.connection import ConnectionEnvironment
    from app.integration.models.provider import IntegrationProvider
    from app.integration.schemas.connection import ConnectionCreate
    from app.integration.services import connections as connection_service
    from app.inventory.models.stock_item import StockItemCondition
    from app.inventory.schemas.stock_item import StockItemCreate
    from app.inventory.services.stock_item import create_stock_item
    from app.sales.models.document import DocumentOwnerType, SalesDocument
    from app.sales.services.offer import create_offer
    from app.valuation.models.valuation import Valuation, ValuationSource
    from app.vehicle.models.configuration import ConfigurationMatchMethod, ConfigurationMode, ConfigurationSource
    from app.vehicle.schemas.configuration import ConfigurationCreate
    from app.vehicle.services import configuration as configuration_service
    from tests.configuration_test_support import MANUAL_CODED, seed_manual_coded_lists
    from tests.identapi_support import make_provider

    customer = client.post(
        "/v1/customers",
        json={
            "firstName": "Anna",
            "lastName": "Muster",
            "language": "de",
            "phones": [{"phoneType": "mobile", "phoneE164": "+41791234567"}],
            "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        },
        headers=idem(headers),
    )
    assert customer.status_code == 201, customer.text
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        tenant = uuid.UUID(dealership_id)
        create_stock_item(
            session,
            tenant_id=tenant,
            data=StockItemCreate(vehicle_label="VW Golf", condition=StockItemCondition.USED),
            actor_id=uuid.UUID(manager["id"]),
        )
        offer = create_offer(session, tenant_id=tenant, actor_id=uuid.UUID(manager["id"]))
        offer.customer_id = uuid.UUID(customer.json()["id"])
        session.add(
            SalesDocument(
                tenant_id=tenant,
                owner_type=DocumentOwnerType.OFFER,
                owner_id=offer.id,
                version=1,
                correspondence_language="de",
                content_definition={"title": "Offerte"},
                rendered_at=offer.created_at,
            )
        )
        session.add(
            Valuation(
                tenant_id=tenant,
                valuation_number=f"B-{uuid.uuid4().hex[:6]}",
                source=ValuationSource.MANUAL,
                final_offer=Decimal("15000.00"),
                valid_from=offer.created_at.date(),
                valid_until=offer.created_at + dt.timedelta(days=30),
                is_draft=False,
            )
        )
        seed_manual_coded_lists(session)
        configuration_service.create_configuration(
            session,
            tenant_id=tenant,
            actor_id=uuid.UUID(manager["id"]),
            data=ConfigurationCreate(
                source=ConfigurationSource.MANUAL,
                mode=ConfigurationMode.RECORD,
                match_method=ConfigurationMatchMethod.MANUAL,
                brand_display_name="Škoda",
                model_group_name="Octavia",
                variant_name="Combi",
                **MANUAL_CODED,
            ),
        )
        provider = session.scalar(
            select(IntegrationProvider).where(IntegrationProvider.provider_code == "auto_i_dat")
        ) or make_provider(session)
        connection_service.create_connection(
            session,
            tenant_id=tenant,
            data=ConnectionCreate(
                provider_id=provider.id,
                display_name="auto-i-dat",
                environment=ConnectionEnvironment.SANDBOX,
                config={"clientId": "nexotec-test-client"},
            ),
            actor_id=uuid.UUID(manager["id"]),
        )
        session.commit()
    finally:
        session.close()


def test_a_seeded_betriebs_export_holds_exactly_its_rows(client, export_store, engine, db_session):
    dealership_id, manager, headers = betrieb_with_manager(client)
    other_id, other_manager, other_headers = betrieb_with_manager(client, "Garage Anders AG")
    _seed(client, engine, dealership_id, headers, manager)
    _seed(client, engine, other_id, other_headers, other_manager)
    assert grant(client, other_id, other_manager, dealership_id).status_code == 201
    job = request_export(client, dealership_id, headers)
    # What the database holds for the Betrieb at the moment the worker takes
    # its snapshot (the export's own later audit rows are after it).
    scope = data_export.scope_for(db_session, db_session.get(Dealership, uuid.UUID(dealership_id)))
    scope = data_export._with_references(db_session, scope)
    expected: dict[tuple[str, str], list[str]] = {}
    for exporter in exporters():
        for spec in exporter.exported_tables():
            if spec.in_archive:
                _, rows = read_rows(db_session, spec, scope)
                expected[(exporter.context, spec.name)] = sorted(str(row.get("id")) for row in rows)
    db_session.rollback()
    produce(engine, job["id"])
    archive = download(client, dealership_id, job["id"], headers)

    filled = Counter()
    for exporter in exporters():
        for spec in exporter.exported_tables():
            if not spec.in_archive:
                assert f"data/{exporter.context}/{spec.name}.json" not in archive.namelist()
                continue
            exported = rows_in(archive, exporter.context, spec.name)
            assert sorted(str(row.get("id")) for row in exported) == expected[(exporter.context, spec.name)], spec.name
            if exported:
                filled[exporter.context] += 1
    # The seed reached every built context's export.
    assert {"core", "platform", "customer", "inventory", "sales", "valuation", "vehicle", "integration"} <= set(filled)
    # What the seed made for this Betrieb, counted by hand — not through the
    # export's own scope functions, which `expected` above relies on.
    by_hand = {
        ("customer", "customer"): 1,
        ("customer", "customer_phone"): 1,
        ("customer", "customer_email"): 1,
        ("inventory", "stock_item"): 1,
        ("vehicle", "vehicle_configuration"): 1,
        ("integration", "integration_connection"): 1,
        ("valuation", "valuation"): 1,
    }
    assert {key: len(rows_in(archive, *key)) for key in by_hand} == by_hand
    # No row of the other Betrieb (its own id never appears as an owner).
    for name in archive.namelist():
        if name.startswith("data/") and name.endswith(".json"):
            for row in rows_in(archive, *name[len("data/") : -len(".json")].split("/")):
                for column in ("tenant_id", "dealership_id"):
                    assert row.get(column) in (None, dealership_id) or (
                        name.endswith("dealership_membership.json") and row["dealership_id"] == dealership_id
                    ), (name, column)

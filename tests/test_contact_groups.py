"""KAN-466 — contact groups (Anto, 2026-10-26): a Nexotec-defined list
(`contact_group`, platform reference data) that each Betrieb switches on in
Dealer Administration; a customer carries several; a group switched off stays
on the customers that carry it, is no longer offered and cannot be set or
removed from that Betrieb; the list filters by group; a merge keeps them; the
sister-Betrieb copy switches the source's groups on in the target.
"""

import importlib.util
import pathlib
import uuid

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import sessionmaker

from app.core.audit_model import AuditEvent
from app.core.base import utcnow
from app.core.uuid7 import uuid7
from app.customer.models.customer import CustomerContactGroup
from app.platform.models.contact_group import DealershipContactGroup
from app.platform.models.reference_data import ReferenceList, ReferenceValue
from app.platform.services.contact_group import SEEDED_CONTACT_GROUPS
from tests.membership_support import bearer, create_dealership, create_user, grant, platform_admin, token

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1] / "alembic/versions/platform/d72281c678c0_kan466_contact_groups.py"
)


@pytest.fixture
def contact_group_list(engine):
    """The seeded list, as the platform migration writes it."""

    now = utcnow()
    with engine.begin() as conn:
        list_id = uuid7()
        conn.execute(
            insert(ReferenceList.__table__).values(
                id=list_id, list_code="contact_group", created_at=now, updated_at=now
            )
        )
        conn.execute(
            insert(ReferenceValue.__table__),
            [
                {
                    "id": uuid7(),
                    "list_id": list_id,
                    "value_code": code,
                    "label_de": de,
                    "label_fr": fr,
                    "label_it": it,
                    "label_en": en,
                    "sort_order": order,
                    "active": True,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for order, (code, de, fr, it, en) in enumerate(SEEDED_CONTACT_GROUPS)
            ],
        )
    return list_id


def _manager(dealership: str) -> dict[str, str]:
    return bearer(token(tenant_id=dealership, is_dealer_manager=True))


def _salesperson(dealership: str) -> dict[str, str]:
    from app.core.auth import AccessRole

    return bearer(token(AccessRole.SALES, tenant_id=dealership))


def _switch(client, dealership, code, enabled, headers=None):
    return client.put(
        f"/v1/dealerships/{dealership}/contact-groups/{code}",
        json={"enabled": enabled},
        headers={**(headers or _manager(dealership)), "Idempotency-Key": str(uuid.uuid4())},
    )


def _groups(client, dealership, headers=None):
    response = client.get(f"/v1/dealerships/{dealership}/contact-groups", headers=headers or _manager(dealership))
    assert response.status_code == 200, response.text
    return {item["valueCode"]: item for item in response.json()["items"]}


def _create_customer(client, dealership, *, headers=None, **fields):
    payload = {
        "firstName": "Anna",
        "lastName": "Muster",
        "language": "de",
        "emails": [{"emailType": "personal", "emailAddress": f"anna-{uuid.uuid4().hex[:8]}@example.ch"}],
        **fields,
    }
    return client.post("/v1/customers", json=payload, headers=headers or _manager(dealership))


def _patch(client, dealership, customer, groups, *, headers=None):
    return client.patch(
        f"/v1/customers/{customer['id']}",
        json={"contactGroups": groups},
        headers={**(headers or _manager(dealership)), "If-Match": str(customer["version"])},
    )


def _get(client, dealership, customer_id):
    response = client.get(f"/v1/customers/{customer_id}", headers=_manager(dealership))
    assert response.status_code == 200, response.text
    return response.json()


# --- the seed ---------------------------------------------------------------------------


def test_the_migration_seeds_exactly_antos_initial_set():
    """ADR-133: the migration's literals equal the application's declaration,
    and the German labels are the ruled set, in order."""

    spec = importlib.util.spec_from_file_location("kan466_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LIST_CODE == "contact_group"
    assert tuple(module.VALUES) == SEEDED_CONTACT_GROUPS
    assert [row[1] for row in SEEDED_CONTACT_GROUPS] == [
        "Privat",
        "Gewerbe",
        "Flotte",
        "Händler",
        "Importeur",
        "Versicherung",
        "Leasing",
        "Behörde / öffentliche Hand",
    ]
    for row in SEEDED_CONTACT_GROUPS:
        assert all(label.strip() for label in row[1:]), row


# --- the switch per Betrieb ---------------------------------------------------------------


def test_no_group_is_on_until_the_manager_switches_it_on(client, contact_group_list):
    dealership = create_dealership(client)
    groups = _groups(client, dealership)
    assert list(groups) == [row[0] for row in SEEDED_CONTACT_GROUPS]
    assert not any(g["enabled"] for g in groups.values())
    assert groups["fleet"]["labelDe"] == "Flotte" and groups["fleet"]["labelFr"] == "Flotte"

    response = _switch(client, dealership, "fleet", True)
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True
    assert _groups(client, dealership)["fleet"]["enabled"] is True


def test_the_switch_is_per_betrieb(client, contact_group_list):
    home = create_dealership(client, "Garage Eins AG")
    sister = create_dealership(client, "Garage Zwei AG", group_of=home)
    assert _switch(client, home, "fleet", True).status_code == 200
    assert _groups(client, home)["fleet"]["enabled"] is True
    assert _groups(client, sister)["fleet"]["enabled"] is False


def test_switching_is_idempotent_and_audited_once(client, engine, contact_group_list):
    dealership = create_dealership(client)
    assert _switch(client, dealership, "leasing", True).status_code == 200
    assert _switch(client, dealership, "leasing", True).status_code == 200
    assert _switch(client, dealership, "leasing", False).status_code == 200
    session = sessionmaker(bind=engine)()
    try:
        rows = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_id == uuid.UUID(dealership), AuditEvent.entity_type == "dealership")
            .where(AuditEvent.action == "update")
            .order_by(AuditEvent.created_at, AuditEvent.id)
        ).all()
        changes = [(r.before, r.after) for r in rows if r.after and "contact_groups" in r.after]
        assert changes == [
            ({"contact_groups": []}, {"contact_groups": ["leasing"]}),
            ({"contact_groups": ["leasing"]}, {"contact_groups": []}),
        ]
        assert session.scalars(select(DealershipContactGroup)).all() == []
    finally:
        session.close()


def test_only_the_manager_or_staff_switch_any_role_reads(client, contact_group_list):
    dealership = create_dealership(client)
    assert _switch(client, dealership, "fleet", True, headers=_salesperson(dealership)).status_code == 403
    assert _groups(client, dealership, headers=_salesperson(dealership))["fleet"]["enabled"] is False
    assert _switch(client, dealership, "fleet", True, headers=platform_admin()).status_code == 200


def test_another_betriebs_switch_is_not_found(client, contact_group_list):
    mine = create_dealership(client, "Garage Eins AG")
    theirs = create_dealership(client, "Garage Fremd AG")
    assert _switch(client, theirs, "fleet", True, headers=_manager(mine)).status_code == 404
    response = client.get(f"/v1/dealerships/{theirs}/contact-groups", headers=_manager(mine))
    assert response.status_code == 404


def test_an_unknown_group_is_not_found_and_a_deactivated_one_cannot_be_switched_on(client, engine, contact_group_list):
    dealership = create_dealership(client)
    assert _switch(client, dealership, "fleet", True).status_code == 200
    assert _switch(client, dealership, "astronauts", True).status_code == 404
    with engine.begin() as conn:
        conn.execute(ReferenceValue.__table__.update().where(ReferenceValue.value_code == "fleet").values(active=False))
    groups = _groups(client, dealership)
    assert groups["fleet"]["active"] is False and groups["fleet"]["enabled"] is False
    response = _switch(client, dealership, "insurer", True)
    assert response.status_code == 200
    with engine.begin() as conn:
        conn.execute(
            ReferenceValue.__table__.update().where(ReferenceValue.value_code == "insurer").values(active=False)
        )
    refused = _switch(client, dealership, "insurer", False)
    assert refused.status_code == 200  # switching off a deactivated group is always allowed
    refused = _switch(client, dealership, "insurer", True)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "contact_group_inactive"


# --- contacts carry groups ----------------------------------------------------------------


def test_a_customer_carries_several_switched_on_groups(client, engine, contact_group_list):
    dealership = create_dealership(client)
    _switch(client, dealership, "fleet", True)
    _switch(client, dealership, "commercial", True)
    created = _create_customer(client, dealership, contactGroups=["fleet", " commercial ", "fleet"])
    assert created.status_code == 201, created.text
    assert created.json()["contactGroups"] == ["commercial", "fleet"]

    patched = _patch(client, dealership, created.json(), ["commercial"])
    assert patched.status_code == 200, patched.text
    assert patched.json()["contactGroups"] == ["commercial"]
    assert _get(client, dealership, created.json()["id"])["contactGroups"] == ["commercial"]

    session = sessionmaker(bind=engine)()
    try:
        update = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == uuid.UUID(created.json()["id"]), AuditEvent.action == "update"
            )
        ).one()
        assert update.before == {"contact_groups": ["commercial", "fleet"]}
        assert update.after == {"contact_groups": ["commercial"]}
    finally:
        session.close()


def test_a_group_that_is_not_switched_on_is_refused(client, contact_group_list):
    dealership = create_dealership(client)
    _switch(client, dealership, "fleet", True)
    refused = _create_customer(client, dealership, contactGroups=["leasing"])
    assert refused.status_code == 422
    assert refused.json()["error"]["details"] == {"reason": "contact_group_not_enabled", "valueCodes": ["leasing"]}
    unknown = _create_customer(client, dealership, contactGroups=["astronauts"])
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"]["reason"] == "contact_group_unknown"

    customer = _create_customer(client, dealership).json()
    refused = _patch(client, dealership, customer, ["leasing"])
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "contact_group_not_enabled"


def test_a_group_switched_off_stays_on_the_contact_read_only(client, contact_group_list):
    dealership = create_dealership(client)
    _switch(client, dealership, "fleet", True)
    _switch(client, dealership, "leasing", True)
    customer = _create_customer(client, dealership, contactGroups=["fleet", "leasing"]).json()

    _switch(client, dealership, "fleet", False)
    assert _get(client, dealership, customer["id"])["contactGroups"] == ["fleet", "leasing"]

    # Removing it from here is refused, never silently kept …
    refused = _patch(client, dealership, customer, ["leasing"])
    assert refused.status_code == 422
    assert refused.json()["error"]["details"] == {"reason": "contact_group_read_only", "valueCodes": ["fleet"]}
    # … and a change to the others keeps it when the body keeps it.
    kept = _patch(client, dealership, customer, ["fleet"])
    assert kept.status_code == 200, kept.text
    assert kept.json()["contactGroups"] == ["fleet"]


def test_a_sister_betriebs_group_is_read_only_here(client, contact_group_list):
    home = create_dealership(client, "Garage Eins AG")
    sister = create_dealership(client, "Garage Zwei AG", group_of=home)
    _switch(client, sister, "insurer", True)
    _switch(client, home, "private", True)
    customer = _create_customer(client, sister, contactGroups=["insurer"]).json()

    # The customer is the group's (ADR-014): the home Betrieb reads it with
    # the sister's group on it, and may add its own but not drop the sister's.
    read = client.get(f"/v1/customers/{customer['id']}", headers=_manager(home))
    assert read.status_code == 200 and read.json()["contactGroups"] == ["insurer"]
    refused = _patch(client, home, customer, [], headers=_manager(home))
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["reason"] == "contact_group_read_only"
    added = _patch(client, home, customer, ["insurer", "private"], headers=_manager(home))
    assert added.status_code == 200, added.text
    assert added.json()["contactGroups"] == ["insurer", "private"]


# --- the list ---------------------------------------------------------------------------


def test_the_list_filters_by_group_and_shows_them(client, contact_group_list):
    dealership = create_dealership(client)
    _switch(client, dealership, "fleet", True)
    _switch(client, dealership, "dealer", True)
    fleet = _create_customer(client, dealership, lastName="Flotte", contactGroups=["fleet"]).json()
    dealer = _create_customer(client, dealership, lastName="Haendler", contactGroups=["dealer", "fleet"]).json()
    none = _create_customer(client, dealership, lastName="Ohne").json()

    def ids(**params):
        response = client.get("/v1/customers", params=params, headers=_manager(dealership))
        assert response.status_code == 200, response.text
        return {item["id"]: item["contactGroups"] for item in response.json()["items"]}

    assert ids(contactGroup="fleet") == {fleet["id"]: ["fleet"], dealer["id"]: ["dealer", "fleet"]}
    assert ids(contactGroup="dealer") == {dealer["id"]: ["dealer", "fleet"]}
    # «ist nicht»: a customer without any group is kept — it is not that group.
    assert set(ids(contactGroupNot="dealer")) == {fleet["id"], none["id"]}


def test_the_list_filter_does_not_reach_another_group(client, contact_group_list):
    mine = create_dealership(client, "Garage Eins AG")
    theirs = create_dealership(client, "Garage Fremd AG")
    _switch(client, theirs, "fleet", True)
    _create_customer(client, theirs, contactGroups=["fleet"])
    response = client.get("/v1/customers", params={"contactGroup": "fleet"}, headers=_manager(mine))
    assert response.status_code == 200
    assert response.json()["items"] == []


# --- merge and copy ---------------------------------------------------------------------


def test_a_merge_carries_the_groups_onto_the_survivor(client, engine, contact_group_list):
    dealership = create_dealership(client)
    for code in ("fleet", "leasing", "private"):
        _switch(client, dealership, code, True)
    survivor = _create_customer(client, dealership, contactGroups=["fleet"]).json()
    duplicate = _create_customer(client, dealership, contactGroups=["fleet", "leasing"]).json()
    _switch(client, dealership, "leasing", False)  # a switched-off group is not lost by a merge either

    merged = client.post(
        f"/v1/customers/{duplicate['id']}/merge",
        json={"duplicateOfCustomerId": survivor["id"]},
        headers={**_manager(dealership), "If-Match": str(duplicate["version"])},
    )
    assert merged.status_code == 200, merged.text
    assert _get(client, dealership, survivor["id"])["contactGroups"] == ["fleet", "leasing"]
    session = sessionmaker(bind=engine)()
    try:
        left = session.scalars(
            select(CustomerContactGroup).where(CustomerContactGroup.customer_id == uuid.UUID(duplicate["id"]))
        ).all()
        assert left == []
        merge_row = session.scalars(
            select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(duplicate["id"]), AuditEvent.action == "merge")
        ).one()
        assert merge_row.after["contactGroupsRepointed"] == 1
        assert merge_row.after["contactGroupsDropped"] == 1
    finally:
        session.close()


def test_copy_from_a_sister_switches_the_sources_groups_on(client, engine, contact_group_list):
    source = create_dealership(client, "Garage Quelle AG")
    target = create_dealership(client, "Garage Ziel AG", group_of=source)
    user = create_user(client, source)
    assert grant(client, source, user, target, is_dealer_manager=True).status_code == 201
    in_target = bearer(token(tenant_id=target, user_id=user["id"], is_dealer_manager=True))
    for code in ("fleet", "leasing", "insurer"):
        _switch(client, source, code, True)
    _switch(client, target, "leasing", True)
    _switch(client, target, "private", True)
    with engine.begin() as conn:
        conn.execute(
            ReferenceValue.__table__.update().where(ReferenceValue.value_code == "insurer").values(active=False)
        )

    def copy(dry_run):
        response = client.post(
            f"/v1/dealerships/{target}/copy-configuration",
            json={"sourceDealershipId": source, "subsets": ["contact_groups"], "dryRun": dry_run},
            headers={**in_target, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 200, response.text
        return {item["key"]: item["outcome"] for item in response.json()["items"]}

    # The source's deactivated «insurer» is not on there any more, so it is not offered for copying.
    assert copy(True) == {"fleet": "created", "leasing": "unchanged"}
    assert _groups(client, target)["fleet"]["enabled"] is False  # the dry run wrote nothing
    assert copy(False) == {"fleet": "created", "leasing": "unchanged"}
    groups = _groups(client, target)
    # Switched on what the source has on; switched nothing off.
    assert {code for code, g in groups.items() if g["enabled"]} == {"fleet", "leasing", "private"}

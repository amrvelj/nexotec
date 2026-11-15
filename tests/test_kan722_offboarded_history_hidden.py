"""KAN-722 (Anto, 2026-11-14 «hide them»; 2026-11-15 answer A and the archive
ruling): an offboarded Betrieb's entries leave the History tab of a group
customer that its sisters read, and exactly those entries are in its own
archive export.

Which entries are the leaver's (`app.core.audit_attribution`): one written
since KAN-722 carries the Betrieb it was written in; an older one without it
is the leaver's when its author's only Betrieb in the group was the leaver.
An entry by someone who also worked in a sister, or by no one, stays.
"""

import ast
import uuid
from pathlib import Path

from app.core.audit import record_audit_event
from app.core.audit_model import AuditEvent
from app.core.export import read_rows
from app.core.export_core import CoreExporter
from app.customer.services import customer as customer_service
from app.customer.services.data_export import CustomerExporter
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.services import data_export
from tests.membership_support import bearer, token
from tests.test_customer_vehicle_party_future_holder import _customer
from tests.test_kan218_offboarded_betrieb_leaves_group_reads import _group, _offboarded, _user

_APP = Path(__file__).resolve().parents[1] / "app"


def _legacy(db_session, customer, actor_id, action: str, after: dict | None = None) -> uuid.UUID:
    """An entry as every customer write recorded it before KAN-722: no acting Betrieb."""

    event = record_audit_event(
        db_session, entity_type="customer", entity_id=customer.id, tenant_id=customer.group_id,
        action=action, actor_id=actor_id, after=after or {"step": action},
    )
    db_session.commit()
    return event.id


def _scene(db_session):
    """A group customer whose History holds an entry of every kind the rule tells apart."""

    group, viewer, leaving, staying = _group(db_session)
    lea = _user(db_session, leaving, "Lea")  # the leaver's own: her only Betrieb in the group
    noah = _user(db_session, leaving, "Noah")  # homed at the leaver, also a member of the viewer
    db_session.add(DealershipMembership(user_id=noah.id, dealership_id=viewer.id))
    sven = _user(db_session, staying, "Sven")
    db_session.commit()
    customer = _customer(db_session, group.id, "Kunde")
    customer.dealership_id = viewer.id
    db_session.commit()

    def credit_block(actor, betrieb, blocked: bool) -> uuid.UUID:
        customer_service.set_credit_block(
            db_session, customer=customer, blocked=blocked, reason="Mahnung offen" if blocked else None,
            actor_id=actor.id, acting_dealership_id=betrieb.id,
        )
        return db_session.query(AuditEvent.id).filter(
            AuditEvent.entity_id == customer.id
        ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).first()[0]

    entries = {
        "written_in_the_leaver": credit_block(lea, leaving, True),
        "written_in_the_viewer_by_a_leaver_user": credit_block(noah, viewer, False),
        "written_in_the_staying_sister": credit_block(sven, staying, True),
        "old_by_the_leavers_own": _legacy(db_session, customer, lea.id, "old_lea"),
        "old_by_someone_also_in_a_sister": _legacy(db_session, customer, noah.id, "old_noah"),
        # A sister's entry that names both Betriebe: it stays, the leaver's name does not.
        "old_by_the_staying_sisters_own": _legacy(
            db_session, customer, sven.id, "old_sven",
            after={"dealershipId": str(leaving.id), "dealership_id": str(staying.id), "advisorId": str(lea.id)},
        ),
        "old_by_no_one": _legacy(db_session, customer, None, "old_system"),
    }
    return group, viewer, leaving, staying, customer, entries, (lea, noah, sven)


_LEAVERS = {"written_in_the_leaver", "old_by_the_leavers_own"}


def _history(db_session, customer) -> set[uuid.UUID]:
    return {e.id for e in customer_service.list_customer_history(
        db_session, group_id=customer.group_id, customer_id=customer.id
    )}


def test_a_customer_write_records_the_betrieb_it_was_made_in(db_session):
    _, _viewer, leaving, staying, _c, entries, _users = _scene(db_session)

    assert db_session.get(AuditEvent, entries["written_in_the_leaver"]).acting_dealership_id == leaving.id
    assert db_session.get(AuditEvent, entries["written_in_the_staying_sister"]).acting_dealership_id == staying.id
    assert db_session.get(AuditEvent, entries["old_by_the_leavers_own"]).acting_dealership_id is None


def test_a_sister_sees_none_of_the_leavers_entries_and_every_other_entry_stays(db_session):
    _, viewer, leaving, staying, customer, entries, (lea, noah, sven) = _scene(db_session)
    assert _history(db_session, customer) == set(entries.values())
    before = customer_service.history_labels(
        db_session, group_id=customer.group_id, dealership_id=viewer.id,
        events=[db_session.get(AuditEvent, entry) for entry in entries.values()],
    )
    assert {str(leaving.id), str(lea.id), str(noah.id), str(staying.id), str(sven.id)} <= set(before)

    _offboarded(db_session, leaving)

    shown = _history(db_session, customer)
    assert shown == {entry for name, entry in entries.items() if name not in _LEAVERS}
    # What stays names neither the leaver nor its users (KAN-218's masking,
    # kept): Noah's entries stay, his name renders as unknown; Sven's entry
    # still names the leaver and Lea (as advisor), and neither is labelled.
    events = [db_session.get(AuditEvent, entry) for entry in shown]
    labels = customer_service.history_labels(
        db_session, group_id=customer.group_id, dealership_id=viewer.id, events=events
    )
    assert str(leaving.id) not in labels and str(lea.id) not in labels and str(noah.id) not in labels
    assert labels[str(sven.id)] == "Sven Muster"
    assert labels[str(staying.id)] == staying.legal_name


def test_the_history_route_hides_the_leavers_entries(client, db_session):
    _, viewer, leaving, _staying, customer, entries, _users = _scene(db_session)
    _offboarded(db_session, leaving)

    response = client.get(
        f"/v1/customers/{customer.id}/audit-log",
        headers=bearer(token(tenant_id=viewer.id, is_dealer_manager=True)),
    )

    assert response.status_code == 200
    assert {uuid.UUID(item["id"]) for item in response.json()["items"]} == {
        entry for name, entry in entries.items() if name not in _LEAVERS
    }


def test_the_leavers_archive_holds_exactly_the_entries_its_sisters_no_longer_see(db_session):
    _, viewer, leaving, _staying, customer, entries, _users = _scene(db_session)
    shown_before = _history(db_session, customer)
    _offboarded(db_session, leaving)
    hidden = shown_before - _history(db_session, customer)

    scope = data_export._with_references(db_session, data_export.scope_for(db_session, leaving))
    (audit_spec,) = CoreExporter().exported_tables()
    _, rows = read_rows(db_session, audit_spec, scope)
    exported = {uuid.UUID(str(row["id"])) for row in rows if str(row["tenant_id"]) == str(customer.group_id)}

    assert hidden == exported == {entries[name] for name in _LEAVERS}
    # Readable on its own: the customer the entries are about is in the archive
    # too, though a sister opened it.
    customer_spec = next(spec for spec in CustomerExporter().exported_tables() if spec.name == "customer")
    _, customers = read_rows(db_session, customer_spec, scope)
    assert str(customer.id) in {str(row["id"]) for row in customers}
    # A sister's archive holds none of them.
    sister_scope = data_export._with_references(db_session, data_export.scope_for(db_session, viewer))
    _, sister_rows = read_rows(db_session, audit_spec, sister_scope)
    assert not exported & {uuid.UUID(str(row["id"])) for row in sister_rows}


def test_every_customer_history_write_names_the_betrieb_it_was_made_in():
    """From KAN-722 on, every entry records its Betrieb (Anto, 2026-11-15):
    each `record_audit_event` call on a customer passes `acting_dealership_id`
    by name — a Betrieb, or `None` for a write no Betrieb made."""

    missing = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None))
                    == "record_audit_event"):
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            entity = keywords.get("entity_type")
            if isinstance(entity, ast.Constant) and entity.value == "customer" and "acting_dealership_id" not in keywords:
                missing.append(f"{path.relative_to(_APP.parent)}:{node.lineno}")
    assert not missing, missing

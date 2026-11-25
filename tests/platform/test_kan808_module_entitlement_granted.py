"""KAN-808 PR-1d (ADR-222 (1), (4)): Platform states
`platform.module_entitlement.granted` in the entitlement change's own
transaction, once per module that becomes active — the platform_admin's PUT
and onboarding alike — and nothing for a module that stays active or is
withdrawn. `scripts/restate_module_entitlements.py` states it again for every
Betrieb holding a module now, dry run by default."""

import uuid

import pytest
from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.core.permissions import ModuleCode
from app.platform.organisation.models.dealership import Dealership, DealershipStatus
from app.platform.organisation.models.module_entitlement import ModuleEntitlement
from app.platform.organisation.services import entitlement as entitlement_service
from app.platform.organisation.services.entitlement_facts import MODULE_ENTITLEMENT_GRANTED_EVENT
from scripts import restate_module_entitlements as restate
from tests.dealership_support import seed_business_reference_lists
from tests.identity_support import create_dealership, platform_admin_headers
from tests.platform.test_platform_onboarding import _request as onboarding_request


def _granted(db, dealership_id) -> list[OutboxMessage]:
    db.expire_all()
    return list(
        db.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.event_type == MODULE_ENTITLEMENT_GRANTED_EVENT,
                OutboxMessage.tenant_id == uuid.UUID(str(dealership_id)),
            )
            .order_by(OutboxMessage.occurred_at, OutboxMessage.id)
        )
    )


def _put(client, dealership_id, modules):
    current = client.get(f"/v1/dealerships/{dealership_id}/entitlements", headers=platform_admin_headers())
    response = client.put(
        f"/v1/dealerships/{dealership_id}/entitlements",
        json={"modules": modules},
        headers={**platform_admin_headers(), "If-Match": str(current.json()["dealershipVersion"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(db, dealership_id, module: ModuleCode) -> ModuleEntitlement:
    return db.scalars(
        select(ModuleEntitlement).where(
            ModuleEntitlement.dealership_id == uuid.UUID(str(dealership_id)), ModuleEntitlement.module == module
        )
    ).one()


def test_each_module_that_becomes_active_is_stated_once(client, db_session):
    dealership_id = create_dealership(client)
    body = _put(client, dealership_id, ["sales", "aftersales"])

    messages = _granted(db_session, dealership_id)
    assert [m.payload["module"] for m in messages] == ["aftersales", "sales"]
    for message in messages:
        row = _row(db_session, dealership_id, ModuleCode(message.payload["module"]))
        assert message.producer == "platform"
        assert message.aggregate_type == "module_entitlement"
        assert message.aggregate_id == row.id
        assert message.payload == {
            "dealershipId": dealership_id,
            "module": row.module.value,
            "validFrom": row.valid_from.isoformat(),
            "entitlementsVersion": body["entitlementsVersion"],
        }


def test_a_module_that_stays_active_or_is_withdrawn_states_nothing(client, db_session):
    dealership_id = create_dealership(client)
    _put(client, dealership_id, ["sales", "aftersales"])
    assert len(_granted(db_session, dealership_id)) == 2

    _put(client, dealership_id, ["sales", "aftersales"])  # unchanged
    _put(client, dealership_id, ["sales"])  # aftersales withdrawn, sales stays
    assert len(_granted(db_session, dealership_id)) == 2


def test_a_re_grant_is_stated_again_with_the_new_version(client, db_session):
    dealership_id = create_dealership(client)
    _put(client, dealership_id, ["aftersales"])
    _put(client, dealership_id, [])
    body = _put(client, dealership_id, ["aftersales", "parts"])

    messages = _granted(db_session, dealership_id)
    assert [m.payload["module"] for m in messages] == ["aftersales", "aftersales", "parts"]
    first, again, parts = messages
    assert again.aggregate_id == first.aggregate_id  # the row is reopened, not replaced
    assert again.id != first.id
    assert again.payload["entitlementsVersion"] == parts.payload["entitlementsVersion"] == body["entitlementsVersion"]
    assert again.payload["entitlementsVersion"] > first.payload["entitlementsVersion"]


def test_the_fact_lands_with_the_change_or_not_at_all(client, db_session):
    dealership_id = uuid.UUID(create_dealership(client))
    dealership = db_session.get(Dealership, dealership_id)
    entitlement_service.set_entitlements(
        db_session, dealership=dealership, modules={ModuleCode.AFTERSALES}, actor_id=uuid.uuid4(), commit=False
    )
    assert len(_granted_in(db_session, dealership_id)) == 1  # in the caller's transaction, not yet committed
    db_session.rollback()

    assert _granted(db_session, dealership_id) == []
    assert db_session.scalars(select(ModuleEntitlement).where(ModuleEntitlement.dealership_id == dealership_id)).all() == []


def _granted_in(db, dealership_id) -> list[OutboxMessage]:
    """The facts visible in `db`'s own open transaction."""

    return list(
        db.scalars(
            select(OutboxMessage).where(
                OutboxMessage.event_type == MODULE_ENTITLEMENT_GRANTED_EVENT, OutboxMessage.tenant_id == dealership_id
            )
        )
    )


def test_onboarding_states_each_module_in_its_one_transaction(client, mailbox, db_session):
    seed_business_reference_lists(db_session)
    response = client.post(
        "/v1/platform/onboarding",
        json=onboarding_request(entitlements=["aftersales", "sales"]),
        headers=platform_admin_headers(),
    )
    assert response.status_code == 201, response.text
    dealership_id = response.json()["dealershipId"]

    messages = _granted(db_session, dealership_id)
    assert [m.payload["module"] for m in messages] == ["aftersales", "sales"]
    onboarded = db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.event_type == "platform.dealership.onboarded")
    ).one()
    assert all(m.payload["entitlementsVersion"] == 1 for m in messages)
    assert {m.tenant_id for m in messages} == {onboarded.tenant_id}


def test_onboarding_without_modules_states_nothing(client, mailbox, db_session):
    seed_business_reference_lists(db_session)
    response = client.post(
        "/v1/platform/onboarding", json=onboarding_request(entitlements=[]), headers=platform_admin_headers()
    )
    assert response.status_code == 201, response.text
    assert _granted(db_session, response.json()["dealershipId"]) == []


# --- the restatement (ADR-222 (4)) ------------------------------------------------


@pytest.fixture()
def holders(client, db_session):
    """Three Betriebe: one holding aftersales, one that held it and gave it up,
    one offboarded while holding it — and one holding sales only."""

    holding = create_dealership(client)
    _put(client, holding, ["aftersales", "sales"])
    withdrawn = create_dealership(client)
    _put(client, withdrawn, ["aftersales"])
    _put(client, withdrawn, [])
    offboarded = create_dealership(client)
    _put(client, offboarded, ["aftersales"])
    db_session.get(Dealership, uuid.UUID(offboarded)).status = DealershipStatus.OFFBOARDED
    db_session.commit()
    sales_only = create_dealership(client)
    _put(client, sales_only, ["sales"])
    return holding, withdrawn, offboarded, sales_only


def _all_granted(db) -> list[OutboxMessage]:
    db.expire_all()
    return list(db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == MODULE_ENTITLEMENT_GRANTED_EVENT)))


def test_the_dry_run_lists_the_holders_and_writes_nothing(client, db_session, holders):
    holding, *_ = holders
    before = len(_all_granted(db_session))

    _correlation, restated = restate.run(db_session, module=ModuleCode.AFTERSALES, commit=False)

    assert [str(grant.dealership_id) for grant in restated] == [holding]
    assert len(_all_granted(db_session)) == before


def test_a_committed_run_states_the_fact_for_each_holder_under_one_correlation(client, db_session, holders):
    holding, withdrawn, offboarded, sales_only = holders
    before = {m.id for m in _all_granted(db_session)}

    correlation_id, restated = restate.run(db_session, module=ModuleCode.AFTERSALES, commit=True)

    new = [m for m in _all_granted(db_session) if m.id not in before]
    assert [str(m.tenant_id) for m in new] == [holding]
    [message] = new
    row = _row(db_session, holding, ModuleCode.AFTERSALES)
    assert message.aggregate_id == row.id == restated[0].entitlement_id
    assert message.correlation_id == correlation_id
    assert message.payload == {
        "dealershipId": holding,
        "module": "aftersales",
        "validFrom": row.valid_from.isoformat(),
        "entitlementsVersion": db_session.get(Dealership, uuid.UUID(holding)).entitlements_version,
    }


def test_the_script_is_a_dry_run_unless_told_to_commit(client, db_session, engine, holders, capsys):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    before = len(_all_granted(db_session))

    assert restate.main(["--module", "aftersales"], session_factory=factory) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert len(_all_granted(db_session)) == before

    assert restate.main(["--module", "aftersales", "--commit"], session_factory=factory) == 0
    assert "COMMITTED" in capsys.readouterr().out
    assert len(_all_granted(db_session)) == before + 1

    assert restate.main(["--module", "workshop"], session_factory=factory) == 2

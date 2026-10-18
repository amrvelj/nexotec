"""KAN-199 (D-A-01, FR-A-13, KAN-135): memberships and the manager flag are
granted and ended through the API, audit-logged with the granter, and the
last-active-manager rule counts active memberships only."""

import uuid

from app.core.auth import AccessRole
from app.platform.models.dealership_membership import DealershipMembership
from app.platform.services import user as user_service
from tests.membership_support import (
    audit_actions,
    bearer,
    create_dealership,
    create_user,
    grant,
    if_match,
    manager_of,
    platform_admin,
    token,
)


def _two_dealerships(client):
    home = create_dealership(client, "Garage Nord AG")
    return home, create_dealership(client, "Garage Süd AG", group_of=home)


# --- granting ----------------------------------------------------------------------


def test_platform_admin_grants_a_membership_with_the_granter_recorded_on_both_tenants(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)

    response = grant(client, home, user, sister, is_dealer_manager=True)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dealershipId"] == sister
    assert body["dealershipLabel"] == "Garage Süd AG"
    assert body["isDealerManager"] is True
    assert body["active"] is True
    assert body["validTo"] is None
    assert body["grantedBy"] is not None

    listed = client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=platform_admin()).json()
    assert [item["dealershipId"] for item in listed["items"]] == [sister]
    # A grant is a child row; the user row is untouched (no If-Match on the
    # POST, like every child-collection POST). The PUTs and DELETE below
    # check and bump it.
    assert listed["userVersion"] == 1

    for tenant in (home, sister):
        actions = [item["action"] for item in audit_actions(client, tenant, user["id"])]
        assert "membership_grant" in actions, (tenant, actions)
    granted = next(item for item in audit_actions(client, sister, user["id"]) if item["action"] == "membership_grant")
    assert granted["actorId"] is not None
    assert granted["after"] == {"dealership_id": sister, "is_dealer_manager": True, "active": True}
    assert granted["before"] is None
    flag = [item for item in audit_actions(client, sister, user["id"]) if item["action"] == "manager_flag_set"]
    assert len(flag) == 1 and flag[0]["after"]["is_dealer_manager"] is True


def test_a_manager_of_both_dealerships_grants_but_a_manager_of_one_side_gets_404(client):
    home, sister = _two_dealerships(client)
    manager = create_user(client, home)  # manager of home only
    user = create_user(client, home, is_dealer_manager=False)

    refused = grant(client, home, user, sister, headers=manager_of(home, manager))
    assert refused.status_code == 404, refused.text
    assert str(sister) in refused.json()["error"]["message"]

    # Platform staff make the same manager a manager of the sister too …
    assert grant(client, home, manager, sister, is_dealer_manager=True).status_code == 201
    # … and now the grant is theirs to make.
    allowed = grant(client, home, user, sister, headers=manager_of(home, manager))
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["grantedBy"] == manager["id"]


def test_the_home_dealership_a_platform_admin_or_a_duplicate_is_refused(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    staff = create_user(client, home, accessRoles=["platform_admin"], isDealerManager=False)

    assert grant(client, home, user, home).status_code == 422
    assert grant(client, home, staff, sister).status_code == 422
    assert grant(client, home, user, sister).status_code == 201
    duplicate = grant(client, home, user, sister)
    assert duplicate.status_code == 409, duplicate.text


def test_granting_platform_admin_to_a_user_with_a_membership_is_refused(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    assert grant(client, home, user, sister).status_code == 201

    response = client.patch(
        f"/v1/dealerships/{home}/users/{user['id']}",
        json={"accessRoles": ["sales", "platform_admin"]},
        headers=if_match(client, home, user),
    )
    assert response.status_code == 422, response.text


def test_a_grant_needs_the_users_tenant_and_an_unknown_target_is_404(client):
    home, _ = _two_dealerships(client)
    user = create_user(client, home)
    assert grant(client, home, user, str(uuid.uuid4())).status_code == 404
    other = create_dealership(client, "Garage Ost AG")
    # The path dealership must be the token's tenant: a manager of another Betrieb sees nothing.
    foreign_manager = create_user(client, other)
    assert grant(client, home, user, other, headers=manager_of(other, foreign_manager)).status_code == 404


# --- ending --------------------------------------------------------------------------


def test_ending_a_membership_keeps_the_row_with_an_end_date_and_the_access_stops(client, db_session):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    assert grant(client, home, user, sister).status_code == 201

    response = client.delete(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert response.status_code == 204, response.text

    listed = client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=platform_admin()).json()
    assert len(listed["items"]) == 1
    assert listed["userVersion"] == 2
    assert listed["items"][0]["active"] is False
    assert listed["items"][0]["validTo"] is not None
    row = db_session.scalar(
        DealershipMembership.__table__.select().where(DealershipMembership.user_id == uuid.UUID(user["id"]))
    )
    assert row is not None  # never deleted (Anto, 2026-10-17)
    # switch-dealership and the token's memberships read this set:
    assert user_service.list_membership_dealership_ids(db_session, user_id=uuid.UUID(user["id"])) == frozenset()
    actions = [item["action"] for item in audit_actions(client, sister, user["id"])]
    assert "membership_end" in actions

    # A second end is 404; a re-grant reopens the same row.
    again = client.delete(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
        headers={**platform_admin(), "If-Match": "2"},
    )
    assert again.status_code == 404
    stale = client.delete(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert stale.status_code == 409
    regrant = grant(client, home, user, sister)
    assert regrant.status_code == 201 and regrant.json()["active"] is True
    assert (
        len(
            client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=platform_admin()).json()[
                "items"
            ]
        )
        == 1
    )


def test_ending_the_membership_of_the_targets_last_manager_is_refused_with_an_explanation(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    assert grant(client, home, user, sister, is_dealer_manager=True).status_code == 201
    # The sister has no manager of its own.
    response = client.delete(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert response.status_code == 400, response.text
    assert "last active manager" in response.json()["error"]["message"]


def test_an_ended_membership_no_longer_counts_as_another_manager(client):
    home, sister = _two_dealerships(client)
    sister_manager = create_user(client, sister)
    user = create_user(client, home)
    assert grant(client, home, user, sister, is_dealer_manager=True).status_code == 201
    # While the membership is active, the sister's own manager may step down.
    assert (
        client.patch(
            f"/v1/dealerships/{sister}/users/{sister_manager['id']}",
            json={"isDealerManager": False},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/dealerships/{sister}/users/{sister_manager['id']}",
            json={"isDealerManager": True},
            headers={**platform_admin(), "If-Match": "2"},
        ).status_code
        == 200
    )
    # Ending the membership: now the sister's manager is the last one.
    assert (
        client.delete(
            f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 204
    )
    assert (
        client.patch(
            f"/v1/dealerships/{sister}/users/{sister_manager['id']}",
            json={"isDealerManager": False},
            headers={**platform_admin(), "If-Match": "3"},
        ).status_code
        == 400
    )


# --- the manager flag ------------------------------------------------------------


def test_the_membership_manager_flag_is_set_and_removed_with_an_audit_row_naming_the_granter(client):
    home, sister = _two_dealerships(client)
    create_user(client, sister)  # the sister's own manager, so removal below is allowed
    user = create_user(client, home)
    assert grant(client, home, user, sister).status_code == 201

    url = f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}/manager-flag"
    response = client.put(url, json={"isDealerManager": True}, headers={**platform_admin(), "If-Match": "1"})
    assert response.status_code == 200, response.text
    assert response.json()["isDealerManager"] is True
    response = client.put(url, json={"isDealerManager": False}, headers={**platform_admin(), "If-Match": "2"})
    assert response.status_code == 200, response.text
    assert response.json()["isDealerManager"] is False

    flags = [item for item in audit_actions(client, sister, user["id"]) if item["action"] == "manager_flag_set"]
    assert [(f["before"]["is_dealer_manager"], f["after"]["is_dealer_manager"]) for f in flags] == [
        (False, True),
        (True, False),
    ]
    assert all(f["actorId"] for f in flags)
    assert (
        client.put(url, json={"isDealerManager": True}, headers={**platform_admin(), "If-Match": "1"}).status_code
        == 409
    )


def test_removing_the_flag_from_the_targets_last_manager_is_refused(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    assert grant(client, home, user, sister, is_dealer_manager=True).status_code == 201
    url = f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}/manager-flag"
    response = client.put(url, json={"isDealerManager": False}, headers={**platform_admin(), "If-Match": "1"})
    assert response.status_code == 400


def test_the_home_manager_flag_endpoint_audits_the_grant_and_guards_the_last_manager(client):
    home, _ = _two_dealerships(client)
    manager = create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)

    url = f"/v1/dealerships/{home}/users/{user['id']}/manager-flag"
    response = client.put(url, json={"isDealerManager": True}, headers={**manager_of(home, manager), "If-Match": "1"})
    assert response.status_code == 200, response.text
    assert response.json()["isDealerManager"] is True
    assert response.json()["version"] == 2
    flags = [item for item in audit_actions(client, home, user["id"]) if item["action"] == "manager_flag_set"]
    assert len(flags) == 1 and flags[0]["actorId"] == manager["id"]

    # Both managers step down? The second removal is the last manager.
    assert (
        client.put(url, json={"isDealerManager": False}, headers={**platform_admin(), "If-Match": "2"}).status_code
        == 200
    )
    last = client.put(
        f"/v1/dealerships/{home}/users/{manager['id']}/manager-flag",
        json={"isDealerManager": False},
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert last.status_code == 400


def test_patching_is_dealer_manager_still_works_and_now_records_the_flag_event(client):
    home, _ = _two_dealerships(client)
    create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)
    response = client.patch(
        f"/v1/dealerships/{home}/users/{user['id']}",
        json={"isDealerManager": True},
        headers={**platform_admin(), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    actions = [item["action"] for item in audit_actions(client, home, user["id"])]
    assert "manager_flag_set" in actions and "update" in actions


def test_a_dealer_manager_cannot_flag_a_staff_account(client):
    home, _ = _two_dealerships(client)
    manager = create_user(client, home)
    staff = create_user(client, home, accessRoles=["platform_admin"], isDealerManager=False)
    response = client.put(
        f"/v1/dealerships/{home}/users/{staff['id']}/manager-flag",
        json={"isDealerManager": True},
        headers={**manager_of(home, manager), "If-Match": "1"},
    )
    assert response.status_code == 403


def test_a_plain_role_cannot_grant_or_list_memberships(client):
    home, sister = _two_dealerships(client)
    user = create_user(client, home)
    plain = bearer(token(AccessRole.SALES, tenant_id=home))
    assert grant(client, home, user, sister, headers=plain).status_code == 403
    assert client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=plain).status_code == 403


def test_a_home_only_manager_sees_no_sister_they_do_not_manage(client):
    home, sister = _two_dealerships(client)
    manager = create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)
    assert grant(client, home, user, sister).status_code == 201

    listed = client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=manager_of(home, manager))
    assert listed.status_code == 200
    assert listed.json()["items"] == []  # not the sister's id, name or granter
    # Platform staff make the manager a manager of the sister too: now they see it.
    assert grant(client, home, manager, sister, is_dealer_manager=True).status_code == 201
    listed = client.get(f"/v1/dealerships/{home}/users/{user['id']}/memberships", headers=manager_of(home, manager))
    assert [item["dealershipLabel"] for item in listed.json()["items"]] == ["Garage Süd AG"]


def test_nothing_is_granted_to_a_deactivated_user(client):
    home, sister = _two_dealerships(client)
    create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{user['id']}",
            json={"status": "deactivated"},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 200
    )
    assert grant(client, home, user, sister).status_code == 409


def test_a_one_side_manager_can_neither_end_nor_flag_a_membership_of_the_other_side(client):
    home, sister = _two_dealerships(client)
    manager = create_user(client, home)  # manager of home only
    user = create_user(client, home, is_dealer_manager=False)
    assert grant(client, home, user, sister).status_code == 201
    headers = {**manager_of(home, manager), "If-Match": "1"}
    assert (
        client.delete(f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}", headers=headers).status_code
        == 404
    )
    assert (
        client.put(
            f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}/manager-flag",
            json={"isDealerManager": True},
            headers=headers,
        ).status_code
        == 404
    )


def test_a_manager_of_both_sides_ends_a_membership(client):
    home, sister = _two_dealerships(client)
    manager = create_user(client, home)
    assert grant(client, home, manager, sister, is_dealer_manager=True).status_code == 201
    user = create_user(client, home, is_dealer_manager=False)
    assert grant(client, home, user, sister, headers=manager_of(home, manager)).status_code == 201
    response = client.delete(
        f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}",
        headers={**manager_of(home, manager), "If-Match": "1"},
    )
    assert response.status_code == 204, response.text
    ended = next(item for item in audit_actions(client, sister, user["id"]) if item["action"] == "membership_end")
    assert ended["actorId"] == manager["id"] and ended["before"]["active"] is True and ended["after"]["active"] is False


def test_no_flag_is_granted_to_a_deactivated_user(client):
    home, sister = _two_dealerships(client)
    create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)
    assert grant(client, home, user, sister).status_code == 201
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{user['id']}",
            json={"status": "deactivated"},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 200
    )
    headers = {**platform_admin(), "If-Match": "2"}
    assert (
        client.put(
            f"/v1/dealerships/{home}/users/{user['id']}/memberships/{sister}/manager-flag",
            json={"isDealerManager": True},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/v1/dealerships/{home}/users/{user['id']}/manager-flag", json={"isDealerManager": True}, headers=headers
        ).status_code
        == 409
    )


def test_patching_the_flag_of_a_deactivated_user_is_refused_like_the_dedicated_endpoint(client):
    home, _ = _two_dealerships(client)
    create_user(client, home)
    user = create_user(client, home, is_dealer_manager=False)
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{user['id']}",
            json={"status": "deactivated"},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 200
    )
    response = client.patch(
        f"/v1/dealerships/{home}/users/{user['id']}",
        json={"isDealerManager": True},
        headers={**platform_admin(), "If-Match": "2"},
    )
    assert response.status_code == 409, response.text
    # The removal direction, and the flag set in the same write as the deactivation.
    manager = create_user(client, home)
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{manager['id']}",
            json={"status": "deactivated"},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{manager['id']}",
            json={"isDealerManager": False},
            headers={**platform_admin(), "If-Match": "2"},
        ).status_code
        == 409
    )
    other = create_user(client, home, is_dealer_manager=False)
    assert (
        client.patch(
            f"/v1/dealerships/{home}/users/{other['id']}",
            json={"status": "deactivated", "isDealerManager": True},
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 409
    )


def test_a_dealer_manager_cannot_touch_a_staff_accounts_leftover_membership(client, db_session):
    """KAN-97 on the membership writes: a staff account's membership (only
    rows older than the 422 can exist — the report lists them) is platform
    staff's to end or flag, never a dealer manager's."""

    home, sister = _two_dealerships(client)
    manager = create_user(client, home)
    assert grant(client, home, manager, sister, is_dealer_manager=True).status_code == 201
    staff = create_user(client, home, accessRoles=["platform_admin"], isDealerManager=False)
    db_session.add(DealershipMembership(user_id=uuid.UUID(staff["id"]), dealership_id=uuid.UUID(sister)))
    db_session.commit()

    headers = {**manager_of(home, manager), "If-Match": "1"}
    assert (
        client.put(
            f"/v1/dealerships/{home}/users/{staff['id']}/memberships/{sister}/manager-flag",
            json={"isDealerManager": True},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/v1/dealerships/{home}/users/{staff['id']}/memberships/{sister}", headers=headers).status_code
        == 403
    )
    # Platform staff may end it.
    assert (
        client.delete(
            f"/v1/dealerships/{home}/users/{staff['id']}/memberships/{sister}",
            headers={**platform_admin(), "If-Match": "1"},
        ).status_code
        == 204
    )

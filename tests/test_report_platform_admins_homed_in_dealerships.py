"""KAN-199: the read-only report of platform_admin users homed in a
dealership — reported, never fixed silently."""

import uuid

import scripts.report_platform_admins_homed_in_dealerships as report
from scripts.report_platform_admins_homed_in_dealerships import platform_admins_homed_in_dealerships
from tests.membership_support import create_dealership, create_user


def test_the_report_names_every_platform_admin_with_their_home_dealership(client, db_session):
    dealership = create_dealership(client, "Garage Report AG")
    create_user(client, dealership)
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)

    found = platform_admins_homed_in_dealerships(db_session)
    mine = [
        (str(user.id), home.legal_name if home else None, sisters)
        for user, home, sisters in found
        if user.id == uuid.UUID(staff["id"])
    ]
    assert mine == [(staff["id"], "Garage Report AG", [])]


def test_main_exits_1_when_a_platform_admin_is_homed_and_0_otherwise(client, db_session, monkeypatch, capsys):
    dealership = create_dealership(client, "Garage Exit AG")
    create_user(client, dealership)
    monkeypatch.setattr(report, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    assert report.main() == 0
    staff = create_user(client, dealership, accessRoles=["platform_admin"], isDealerManager=False)
    assert report.main() == 1
    out = capsys.readouterr().out
    assert staff["email"] in out and "Garage Exit AG" in out and "memberships:" not in out

    from app.platform.models.dealership_membership import DealershipMembership

    sister = create_dealership(client, "Garage Exit Sister AG", group_of=dealership)
    db_session.add(DealershipMembership(user_id=uuid.UUID(staff["id"]), dealership_id=uuid.UUID(sister)))
    db_session.commit()
    assert report.main() == 1
    assert "memberships: Garage Exit Sister AG" in capsys.readouterr().out


def test_the_report_names_the_sister_memberships_an_existing_staff_account_holds(client, db_session):
    """The rows KAN-199 refuses from now on: a staff account that already
    holds a membership is listed with it, so each case can be decided."""

    from app.platform.models.dealership_membership import DealershipMembership

    home = create_dealership(client, "Garage Home AG")
    sister = create_dealership(client, "Garage Sister AG", group_of=home)
    create_user(client, home)
    staff = create_user(client, home, accessRoles=["platform_admin"], isDealerManager=False)
    db_session.add(DealershipMembership(user_id=uuid.UUID(staff["id"]), dealership_id=uuid.UUID(sister)))
    db_session.commit()
    found = {
        str(user.id): [d.legal_name for d in sisters]
        for user, _, sisters in platform_admins_homed_in_dealerships(db_session)
    }
    assert found[staff["id"]] == ["Garage Sister AG"]

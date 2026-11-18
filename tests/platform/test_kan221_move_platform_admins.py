"""KAN-221 PR-1 — `scripts/move_platform_admins_to_platform_tenant.py`: the
confirmed move of a staff account homed in a dealer's Betrieb (Anto,
2026-11-14 Q2 A; 2026-11-17 option 1). Dry run by default; only the rows of
the confirmation file act; each row needs its own new Nexotec address, never
one already in use; the Betrieb account keeps its address and history and
loses `platform_admin`."""

import uuid

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.platform.identity.models.user import User
from app.platform.organisation.models.dealership import PLATFORM_DEALERSHIP_ID
from scripts import move_platform_admins_to_platform_tenant as move
from tests.identity_support import PasswordLogin, create_dealership, create_user, last_mail_to
from tests.platform_tenant_support import create_legacy_staff, create_staff, ensure_platform_tenant


@pytest.fixture()
def scene(client, mailbox):
    ensure_platform_tenant(client)
    dealership = create_dealership(client)
    create_user(client, dealership)  # the Betrieb's manager
    actor = create_staff(client)
    PasswordLogin(client, mailbox).accept_invitation(actor)  # an active staff member confirms
    legacy = create_legacy_staff(client, dealership, email="lea@garage.ch")
    _with_dealer_role(client, legacy)
    return dealership, actor, legacy


def _with_dealer_role(client, user: dict) -> None:
    """The Betrieb account keeps a role of its own after the move."""

    factory = client.app.dependency_overrides
    from app.db import get_session_factory

    with factory[get_session_factory]()() as db:
        row = db.get(User, uuid.UUID(user["id"]))
        row.access_roles = ["platform_admin", "sales"]
        db.commit()


def _run(db_session, mailbox, text: str, actor: dict, *, apply: bool):
    rows = move.parse_confirmation_file(text)
    return move.run(db_session, rows, actor_id=uuid.UUID(actor["id"]), commit=apply, mailer=_Mailer(mailbox))


class _Mailer:
    """The recording platform sender the `mailbox` fixture reads."""

    def __init__(self, mailbox):
        self.mailbox = mailbox

    def send(self, mail):
        self.mailbox.append(mail)


def _users_with_email(db_session, email: str) -> list[User]:
    return list(db_session.scalars(select(User).where(User.email == email)).all())


def test_a_dry_run_reports_and_writes_nothing(client, db_session, mailbox, scene):
    _dealership, actor, legacy = scene
    outcomes = _run(db_session, mailbox, f"{legacy['id']},lea@nexotec.ch\n", actor, apply=False)
    assert [(o.moved, o.message) for o in outcomes] == [(False, "would move: new platform account lea@nexotec.ch")]
    db_session.expire_all()
    assert _users_with_email(db_session, "lea@nexotec.ch") == []
    assert "platform_admin" in db_session.get(User, uuid.UUID(legacy["id"])).access_roles


def test_apply_opens_the_platform_account_and_takes_the_role_from_the_betrieb_account(
    client, db_session, mailbox, scene
):
    dealership, actor, legacy = scene
    outcomes = _run(db_session, mailbox, f"# confirmed by Anto\n{legacy['id']},Lea@Nexotec.ch\n", actor, apply=True)
    [outcome] = outcomes
    assert outcome.moved, outcome.message
    db_session.expire_all()

    [new_account] = _users_with_email(db_session, "lea@nexotec.ch")
    assert new_account.id == outcome.new_user_id
    assert new_account.tenant_id == PLATFORM_DEALERSHIP_ID
    assert new_account.access_roles == ["platform_admin"]
    assert (new_account.first_name, new_account.last_name) == (legacy["firstName"], legacy["lastName"])
    assert new_account.status.value == "invited"
    assert last_mail_to(mailbox, "lea@nexotec.ch") is not None  # the invitation to the new address

    betrieb_account = db_session.get(User, uuid.UUID(legacy["id"]))
    assert betrieb_account.tenant_id == uuid.UUID(dealership)  # its history stays where it is
    assert betrieb_account.email == "lea@garage.ch"  # keeps its own address and sign-in
    assert betrieb_account.access_roles == ["sales"]

    # Both writes name the confirming staff member, never the system.
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id.in_([new_account.id, betrieb_account.id]))
    ).all()
    assert {(e.entity_id, e.action) for e in events} >= {(new_account.id, "create"), (betrieb_account.id, "update")}
    moved = [
        e
        for e in events
        if (e.entity_id, e.action) in {(new_account.id, "create"), (betrieb_account.id, "update")}
    ]
    assert {e.actor_id for e in moved} == {uuid.UUID(actor["id"])}
    assert any(e.reason == move.MOVE_REASON for e in moved if e.entity_id == betrieb_account.id)

    # The row no longer holds: a second run refuses it.
    [again] = _run(db_session, mailbox, f"{legacy['id']},lea2@nexotec.ch\n", actor, apply=True)
    assert again.message == "refused: does not hold platform_admin"


def test_only_the_confirmed_rows_act(client, db_session, mailbox, scene):
    dealership, actor, legacy = scene
    other = create_legacy_staff(client, dealership, email="otto@garage.ch")
    _with_dealer_role(client, other)
    _run(db_session, mailbox, f"{legacy['id']},lea@nexotec.ch\n", actor, apply=True)
    db_session.expire_all()
    assert "platform_admin" in db_session.get(User, uuid.UUID(other["id"])).access_roles


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("{id}", "refused: no new address — option 1 needs a separate Nexotec address"),
        ("{id},", "refused: no new address — option 1 needs a separate Nexotec address"),
        ("{id},lea@garage.ch", "refused: the address lea@garage.ch is already in use (uq_user_email)"),
        ("{id},kein-mail", "refused: not an e-mail address: 'kein-mail'"),
        ("nicht-eine-id,lea@nexotec.ch", "refused: not a user id: 'nicht-eine-id'"),
    ],
)
def test_a_row_that_does_not_hold_is_refused_and_nothing_is_written(client, db_session, mailbox, scene, line, message):
    _dealership, actor, legacy = scene
    [outcome] = _run(db_session, mailbox, line.format(id=legacy["id"]) + "\n", actor, apply=True)
    assert (outcome.moved, outcome.message) == (False, message)
    db_session.expire_all()
    assert "platform_admin" in db_session.get(User, uuid.UUID(legacy["id"])).access_roles
    assert db_session.scalars(select(User).where(User.tenant_id == PLATFORM_DEALERSHIP_ID)).all() == [
        db_session.get(User, uuid.UUID(actor["id"]))
    ]


def test_an_address_already_taken_by_another_user_is_refused(client, db_session, mailbox, scene):
    dealership, actor, legacy = scene
    create_user(client, dealership, email="taken@nexotec.ch", isDealerManager=False)
    [outcome] = _run(db_session, mailbox, f"{legacy['id']},taken@nexotec.ch\n", actor, apply=True)
    assert outcome.message == "refused: the address taken@nexotec.ch is already in use (uq_user_email)"


def test_staff_already_in_the_platform_tenant_and_dealer_users_are_refused(client, db_session, mailbox, scene):
    dealership, actor, _legacy = scene
    colleague = create_staff(client)
    plain = create_user(client, dealership, isDealerManager=False)
    lines = f"{colleague['id']},c@nexotec.ch\n{plain['id']},p@nexotec.ch\n{uuid.uuid4()},x@nexotec.ch\n"
    messages = [o.message for o in _run(db_session, mailbox, lines, actor, apply=True)]
    assert messages == [
        "refused: is not homed in a dealer's Betrieb",
        "refused: does not hold platform_admin",
        "refused: no such user",
    ]


def test_a_betrieb_account_left_without_any_role_is_refused(client, db_session, mailbox, scene):
    dealership, actor, _legacy = scene
    bare = create_legacy_staff(client, dealership)  # platform_admin only
    [outcome] = _run(db_session, mailbox, f"{bare['id']},bare@nexotec.ch\n", actor, apply=True)
    assert outcome.message.startswith("refused: holds no role besides platform_admin")


def test_the_run_needs_the_platform_tenant_and_a_staff_actor(client, db_session, mailbox):
    dealership = create_dealership(client)
    manager = create_user(client, dealership)
    with pytest.raises(move.CannotStart, match="platform tenant does not exist"):
        _run(db_session, mailbox, "", manager, apply=False)
    ensure_platform_tenant(client)
    with pytest.raises(move.CannotStart, match="platform_admin"):
        _run(db_session, mailbox, "", manager, apply=False)


def test_main_is_a_dry_run_unless_asked_and_exits_1_on_a_refused_row(
    client, db_session, mailbox, scene, tmp_path, monkeypatch, capsys
):
    _dealership, actor, legacy = scene
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(move, "get_mailer", lambda: _Mailer(mailbox))
    confirmed = tmp_path / "confirmed.csv"
    confirmed.write_text(f"{legacy['id']},lea@nexotec.ch\n", encoding="utf-8")

    assert move.main([str(confirmed), "--actor", actor["id"]], session_factory=lambda: db_session) == 0
    assert "dry run, nothing written" in capsys.readouterr().out
    db_session.expire_all()
    assert _users_with_email(db_session, "lea@nexotec.ch") == []

    assert move.main([str(confirmed), "--actor", actor["id"], "--commit"], session_factory=lambda: db_session) == 0
    assert "1 moved (committed)" in capsys.readouterr().out
    assert move.main([str(confirmed), "--actor", actor["id"], "--commit"], session_factory=lambda: db_session) == 1
    assert move.main([str(tmp_path / "missing.csv"), "--actor", actor["id"]], session_factory=lambda: db_session) == 2

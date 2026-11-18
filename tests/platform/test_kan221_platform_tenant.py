"""KAN-221 PR-1 — the platform tenant (D-A-05, D-A-24; Anto 2026-11-14 Q2 A,
2026-11-17 option 1).

Nexotec's staff are homed in one Betrieb of kind `platform`, in its own group
«Nexotec», created by the platform migration. It is no dealer: never listed,
never a membership, support-session, entitlement or export target, never in
a nightly status or checklist run, never a group onboarded into. Only
`platform_admin` holders live there, and `platform_admin` is granted nowhere
else."""

import importlib.util
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.core.auth import AccessRole, create_access_token
from app.core.errors import UnprocessableEntityError
from app.platform.administration.services import setup_checklist_regressions, status_notifications
from app.platform.identity.models.user import User
from app.platform.organisation.models.dealership import (
    PLATFORM_DEALER_GROUP_ID,
    PLATFORM_DEALERSHIP_ID,
    PLATFORM_TENANT_NAME,
    Dealership,
    DealershipKind,
)
from app.platform.organisation.services import dealership as dealership_service
from app.platform.organisation.services import entitlement as entitlement_service
from scripts.check_concurrent_alembic_upgrade import UPGRADE_TIMEOUT_S, libpq_url, sqlalchemy_url_for
from tests.dealership_support import valid_uid
from tests.identity_support import (
    PasswordLogin,
    create_dealership,
    create_user,
    platform_admin_headers,
)
from tests.platform_tenant_support import create_staff, ensure_platform_tenant, platform_tenant_rows
from tests.token_support import dealer_group_of

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATION = next((_REPO_ROOT / "alembic" / "versions" / "platform").glob("ee50884b1b56_*.py"))
_PLATFORM = str(PLATFORM_DEALERSHIP_ID)


def _load_migration():
    spec = importlib.util.spec_from_file_location("kan221_migration_under_test", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _error(response) -> dict:
    return response.json()["error"]


# --- the migration and its literals (ADR-133) ------------------------------------------------------


def test_the_migration_writes_the_models_platform_tenant():
    migration = _load_migration()
    assert uuid.UUID(migration.PLATFORM_DEALER_GROUP_ID) == PLATFORM_DEALER_GROUP_ID
    assert uuid.UUID(migration.PLATFORM_DEALERSHIP_ID) == PLATFORM_DEALERSHIP_ID
    assert migration.PLATFORM_TENANT_NAME == PLATFORM_TENANT_NAME
    group, dealership = platform_tenant_rows()
    assert (group.id, group.name) == (PLATFORM_DEALER_GROUP_ID, PLATFORM_TENANT_NAME)
    assert (dealership.kind, dealership.legal_name, dealership.dealer_group_id) == (
        DealershipKind.PLATFORM,
        PLATFORM_TENANT_NAME,
        PLATFORM_DEALER_GROUP_ID,
    )


@pytest.fixture
def scratch_url():
    base = os.environ["DMS_TEST_DATABASE_URL"]
    name = "dms_kan221_" + uuid.uuid4().hex[:12]
    admin_url = libpq_url(base, "postgres")
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield sqlalchemy_url_for(base, name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=dict(os.environ, DMS_DATABASE_URL=url),
        capture_output=True,
        text=True,
        timeout=UPGRADE_TIMEOUT_S,
        check=False,
    )


@pytest.mark.skipif(not os.environ.get("DMS_TEST_DATABASE_URL"), reason="Postgres-only (ADR-011)")
def test_the_migration_creates_one_platform_tenant_keeps_dealers_and_reverses(scratch_url):
    before = _load_migration().down_revision
    assert _alembic(scratch_url, "upgrade", before).returncode == 0
    dealer_id, group_id = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(scratch_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dealer_group (id, name, group_read_enabled, version, created_at, updated_at) "
                    "VALUES (:id, 'Gruppe', false, 1, now(), now())"
                ),
                {"id": group_id},
            )
            conn.execute(
                text(
                    "INSERT INTO dealership (id, dealer_group_id, legal_name, franchise_type, address_street, "
                    "address_house_number, address_postal_code, address_locality, address_canton, address_country, "
                    "phone, uid, vat_liable, status, brand_primary_color, default_correspondence_language, "
                    "entitlements_version, version, created_at, updated_at) VALUES (:id, :group, 'Garage Alt AG', "
                    "'independent', 'Bahnhofstrasse', '1', '8001', 'Zürich', 'ZH', 'CH', '+41441234567', :uid, false, "
                    "'active', '#7C3AED', 'de', 0, 1, now(), now())"
                ),
                {"id": dealer_id, "group": group_id, "uid": valid_uid()},
            )

        def rows():
            with engine.connect() as conn:
                return conn.execute(
                    text("SELECT id, kind, dealer_group_id, status, legal_name FROM dealership ORDER BY kind")
                ).all()

        assert _alembic(scratch_url, "upgrade", "heads").returncode == 0
        dealer, platform = rows()
        assert (dealer.id, dealer.kind) == (dealer_id, "dealer")  # an existing Betrieb is a dealer
        assert (platform.id, platform.kind, platform.dealer_group_id) == (
            PLATFORM_DEALERSHIP_ID,
            "platform",
            PLATFORM_DEALER_GROUP_ID,
        )
        assert (platform.status, platform.legal_name) == ("active", "Nexotec")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM dealer_group WHERE id = :id"), {"id": PLATFORM_DEALER_GROUP_ID}).scalar_one() == "Nexotec"
            # Nothing a dealer gets: no location, settings, number ranges, entitlements.
            for table in ("location", "dealership_settings", "number_range", "module_entitlement"):
                count = conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE {'tenant_id' if table in ('location', 'number_range') else 'dealership_id'} = :id"),
                    {"id": PLATFORM_DEALERSHIP_ID},
                ).scalar_one()
                assert count == 0, table

        # A second platform row is refused by the schema itself.
        with pytest.raises(Exception, match="uq_dealership_one_platform"), engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dealership (id, dealer_group_id, kind, legal_name, franchise_type, address_street, "
                    "address_house_number, address_postal_code, address_locality, address_canton, address_country, "
                    "phone, vat_liable, status, brand_primary_color, default_correspondence_language, "
                    "entitlements_version, version, created_at, updated_at) VALUES (:id, :group, 'platform', 'X', "
                    "'independent', '', '', '', '', '', 'CH', '', false, 'active', '#7C3AED', 'de', 0, 1, now(), now())"
                ),
                {"id": uuid.uuid4(), "group": PLATFORM_DEALER_GROUP_ID},
            )

        # Down and up again: reversible, and the re-run creates no second row.
        current = _load_migration().revision
        assert _alembic(scratch_url, "downgrade", f"{current}-1").returncode == 0
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id FROM dealership")).scalars().all() == [dealer_id]
            assert conn.execute(
                text("SELECT count(*) FROM dealer_group WHERE id = :id"), {"id": PLATFORM_DEALER_GROUP_ID}
            ).scalar_one() == 0
        assert _alembic(scratch_url, "upgrade", "heads").returncode == 0
        assert [row.kind for row in rows()] == ["dealer", "platform"]

        # A downgrade never deletes a staff account: it refuses while one is homed there.
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO "user" (id, tenant_id, first_name, last_name, email, role, access_roles, '
                    "is_dealer_manager, status, employment_status, auth_identity_id, commission_relevant, version, "
                    "created_at, updated_at) VALUES (:id, :tenant, 'Ida', 'Staff', 'ida@nexotec.ch', 'admin', "
                    "'[\"platform_admin\"]', false, 'invited', 'active', :sub, false, 1, now(), now())"
                ),
                {"id": (staff_id := uuid.uuid4()), "tenant": PLATFORM_DEALERSHIP_ID, "sub": f"local:{staff_id}"},
            )
        refused = _alembic(scratch_url, "downgrade", f"{current}-1")
        assert refused.returncode != 0 and "homed in the platform tenant" in refused.stderr
    finally:
        engine.dispose()


# --- staff live there, and only staff ---------------------------------------------------------------


@pytest.fixture()
def login(client, mailbox) -> PasswordLogin:
    return PasswordLogin(client, mailbox)


def test_staff_are_created_in_the_platform_tenant_and_sign_in_there(client, login):
    staff = create_staff(client)
    assert staff["dealershipId"] == _PLATFORM and staff["accessRoles"] == ["platform_admin"]
    login.accept_invitation(staff)
    response = login.login(staff)
    assert response.status_code == 200, response.text
    # Staff owe a second factor (D-A-36); the session minted for enrolment is the platform tenant's.
    assert response.json()["session"]["activeDealership"]["id"] == _PLATFORM


def test_the_platform_tenant_homes_staff_only_and_platform_admin_lives_nowhere_else(client):
    platform = ensure_platform_tenant(client)
    dealership = create_dealership(client)
    create_user(client, dealership)

    def post(tenant, roles):
        return client.post(
            f"/v1/dealerships/{tenant}/users",
            json={
                "firstName": "Ida",
                "lastName": "Muster",
                "email": f"ida-{uuid.uuid4().hex[:8]}@example.ch",
                "role": "admin",
                "accessRoles": roles,
            },
            headers=platform_admin_headers(),
        )

    not_staff = post(platform, ["sales"])
    assert not_staff.status_code == 422, not_staff.text
    assert _error(not_staff)["details"]["reason"] == "platform_tenant_holds_staff_only"
    elsewhere = post(dealership, ["platform_admin"])
    assert elsewhere.status_code == 422, elsewhere.text
    assert _error(elsewhere)["details"]["reason"] == "platform_admin_outside_platform_tenant"
    assert post(platform, ["platform_admin"]).status_code == 201


# --- never a dealer --------------------------------------------------------------------------------


def test_the_platform_tenant_is_never_listed_nor_its_group_offered(client):
    ensure_platform_tenant(client)
    dealership = create_dealership(client)

    listed = client.get("/v1/dealerships?limit=100", headers=platform_admin_headers())
    assert listed.status_code == 200, listed.text
    ids = [row["id"] for row in listed.json()["items"]]
    assert dealership in ids and _PLATFORM not in ids

    groups = client.get("/v1/platform/dealer-groups?limit=100", headers=platform_admin_headers())
    assert groups.status_code == 200, groups.text
    names = [row["id"] for row in groups.json()["items"]]
    assert str(PLATFORM_DEALER_GROUP_ID) not in names and str(dealer_group_of(dealership)) in names


def test_the_dealer_routes_answer_404_for_the_platform_tenant(client):
    ensure_platform_tenant(client)
    staff = platform_admin_headers(tenant_id=PLATFORM_DEALERSHIP_ID)
    for path in (
        f"/v1/dealerships/{_PLATFORM}",
        f"/v1/dealerships/{_PLATFORM}/entitlements",
        f"/v1/dealerships/{_PLATFORM}/settings",
        f"/v1/dealerships/{_PLATFORM}/bank-accounts",
        f"/v1/dealerships/{_PLATFORM}/exports",
    ):
        assert client.get(path, headers=staff).status_code == 404, path
    entitlements = client.put(
        f"/v1/dealerships/{_PLATFORM}/entitlements",
        json={"modules": ["sales"]},
        headers={**staff, "If-Match": "1"},
    )
    assert entitlements.status_code == 404, entitlements.text


def test_the_platform_tenant_holds_no_entitlement_even_through_the_service(client, db_session):
    ensure_platform_tenant(client)
    platform = db_session.get(Dealership, PLATFORM_DEALERSHIP_ID)
    with pytest.raises(UnprocessableEntityError, match="no module entitlements"):
        entitlement_service.set_entitlements(db_session, dealership=platform, modules=set(), actor_id=uuid.uuid4())
    db_session.rollback()


def test_no_betrieb_is_created_in_the_platform_group(client, db_session):
    ensure_platform_tenant(client)
    response = client.post(
        "/v1/dealerships",
        json={
            "legalName": "Garage Eingeschmuggelt AG",
            "franchiseType": "independent",
            "address": {"street": "Weg", "houseNumber": "1", "postalCode": "8001", "locality": "Zürich", "canton": "ZH"},
            "phone": "+41441234567",
            "uid": valid_uid(),
            "dealerGroupId": str(PLATFORM_DEALER_GROUP_ID),
        },
        headers=platform_admin_headers(),
    )
    assert response.status_code == 422, response.text
    assert _error(response)["details"]["reason"] == "platform_group_holds_no_dealer"
    assert len(dealership_service.list_group_dealerships(db_session, dealer_group_id=PLATFORM_DEALER_GROUP_ID)) == 0


def test_the_platform_tenant_is_never_a_membership_target(client):
    ensure_platform_tenant(client)
    dealership = create_dealership(client)
    user = create_user(client, dealership, isDealerManager=False)
    create_user(client, dealership)
    response = client.post(
        f"/v1/dealerships/{dealership}/users/{user['id']}/memberships",
        json={"dealershipId": _PLATFORM},
        headers={**platform_admin_headers(), "Idempotency-Key": str(uuid.uuid4()), "If-Match": "1"},
    )
    assert response.status_code == 404, response.text


def test_a_user_homed_in_the_platform_tenant_is_never_a_support_session_target(client, db_session):
    """Only staff live there, and staff are refused as platform_admin; a row
    that slipped past that rule is refused for its home all the same."""

    staff = create_staff(client)
    row = User(
        tenant_id=PLATFORM_DEALERSHIP_ID,
        first_name="Ohne",
        last_name="Rolle",
        email=f"ohne-{uuid.uuid4().hex[:6]}@nexotec.ch",
        role="admin",
        access_roles=["sales"],
        is_dealer_manager=False,
        auth_identity_id=f"local:{uuid.uuid4()}",
        status="active",
    )
    db_session.add(row)
    db_session.commit()
    response = client.post(
        "/v1/platform/support-sessions",
        json={"targetUserId": str(row.id), "reason": "Der Betrieb meldet einen Fehler — Ticket 4711."},
        headers={
            **platform_admin_headers(tenant_id=PLATFORM_DEALERSHIP_ID, user_id=uuid.UUID(staff["id"])),
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422, response.text
    assert _error(response)["details"]["reason"] == "support_target_platform_tenant"


def test_the_nightly_runs_over_every_betrieb_skip_the_platform_tenant(client, db_session, monkeypatch):
    ensure_platform_tenant(client)
    dealership = create_dealership(client)
    db_session.get(Dealership, uuid.UUID(dealership)).activated_at = db_session.get(
        Dealership, PLATFORM_DEALERSHIP_ID
    ).created_at
    db_session.get(Dealership, PLATFORM_DEALERSHIP_ID).activated_at = db_session.get(
        Dealership, PLATFORM_DEALERSHIP_ID
    ).created_at
    db_session.commit()

    for module in (status_notifications, setup_checklist_regressions):
        seen: list[uuid.UUID] = []

        def record(db, dealership, *, _seen=seen, **_kwargs):
            _seen.append(dealership.id)
            raise RuntimeError("recorded")  # logged and rolled back by the run; nothing else happens

        monkeypatch.setattr(module, "check_dealership", record)
        module.run(db_session)
        assert uuid.UUID(dealership) in seen and PLATFORM_DEALERSHIP_ID not in seen, module.__name__


def test_the_report_leaves_staff_homed_in_the_platform_tenant_out(client, db_session):
    from scripts.report_platform_admins_homed_in_dealerships import platform_admins_homed_in_dealerships

    staff = create_staff(client)
    assert staff["id"] not in {str(user.id) for user, _home, _sisters in platform_admins_homed_in_dealerships(db_session)}


def test_the_dealer_configuration_routes_answer_404_for_the_platform_tenant(client, db_session):
    """Review finding (2026-11-18): locations, the publishing profile, number
    ranges, contact groups, the setup checklist, the status page, the document
    template, branding and the shared mailbox are dealer configuration — to them
    the platform tenant is not a Betrieb. A location written there would also
    publish a KAN-226 fact for a Betrieb no channel may ever carry."""

    from sqlalchemy import func, select

    from app.core.outbox_model import OutboxMessage

    ensure_platform_tenant(client)
    staff = platform_admin_headers(tenant_id=PLATFORM_DEALERSHIP_ID)
    for path in (
        f"/v1/dealerships/{_PLATFORM}/locations",
        f"/v1/dealerships/{_PLATFORM}/publishing-profile",
        f"/v1/dealerships/{_PLATFORM}/publishing-profile/effective",
        f"/v1/dealerships/{_PLATFORM}/number-ranges",
        f"/v1/dealerships/{_PLATFORM}/contact-groups",
        f"/v1/dealerships/{_PLATFORM}/setup-checklist",
        f"/v1/dealerships/{_PLATFORM}/status",
        f"/v1/dealerships/{_PLATFORM}/document-template",
        f"/v1/dealerships/{_PLATFORM}/branding",
        f"/v1/dealerships/{_PLATFORM}/mail/business-mailbox",
    ):
        assert client.get(path, headers=staff).status_code == 404, path
    created = client.post(
        f"/v1/dealerships/{_PLATFORM}/locations",
        json={
            "code": "hq",
            "name": "Nexotec",
            "locationTypes": ["showroom"],
            "addressStreet": "Bahnhofstrasse",
            "addressHouseNumber": "1",
            "addressPostalCode": "8001",
            "addressLocality": "Zürich",
            "addressCanton": "ZH",
        },
        headers=staff,
    )
    assert created.status_code == 404, created.text
    link = client.post(
        f"/v1/dealerships/{_PLATFORM}/mail/business-mailbox/links",
        json={"provider": "microsoft_365_mail", "mailboxAddress": "info@nexotec.ch"},
        headers=staff,
    )
    assert link.status_code == 404, link.text
    facts = db_session.scalar(
        select(func.count()).select_from(OutboxMessage).where(OutboxMessage.event_type.like("platform.location.%"))
    )
    assert facts == 0


def test_a_token_for_the_platform_tenant_reads_its_own_users(client):
    """Staff manage their colleagues in their own tenant through the ordinary
    users routes (the Platform users panel is PR 6)."""

    staff = create_staff(client)
    token = create_access_token(
        user_id=uuid.UUID(staff["id"]),
        tenant_id=PLATFORM_DEALERSHIP_ID,
        group_id=PLATFORM_DEALER_GROUP_ID,
        roles=frozenset({AccessRole.PLATFORM_ADMIN}),
        amr=("pwd", "otp", "mfa"),
        mfa=True,
    )
    response = client.get(
        f"/v1/dealerships/{_PLATFORM}/users/{staff['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text

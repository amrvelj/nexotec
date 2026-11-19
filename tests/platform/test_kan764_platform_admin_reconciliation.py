"""KAN-764 (rule 10; KAN-221 follow-up): platform's nightly reconciliation
reports every platform_admin homed outside the platform tenant — a legacy
holder the move script has not moved yet, or one a direct database fix wrote
— and never the staff homed where they belong, nor a dealer's own users.
Reported, never moved (the move script's confirmed-rows rule decides)."""

import uuid

from sqlalchemy import select

from app.core.reconciliation import ReconciliationAlarm
from app.core.reconciliation_model import ReconciliationOrphan
from app.platform import reconciliation as platform_reconciliation
from app.platform.identity.models.user import User
from tests.membership_support import create_dealership, create_user
from tests.platform_tenant_support import create_legacy_staff, create_staff

CHECK = "user.platform_admin_homed_outside_the_platform_tenant"


def _findings(db_session) -> set[uuid.UUID]:
    try:
        run_id = platform_reconciliation.run(db_session).id
    except ReconciliationAlarm as alarm:
        run_id = alarm.run.id
    return set(
        db_session.scalars(
            select(ReconciliationOrphan.source_row_id).where(
                ReconciliationOrphan.run_id == run_id, ReconciliationOrphan.check_label == CHECK
            )
        ).all()
    )


def test_a_platform_admin_homed_in_a_dealer_betrieb_is_reported(client, db_session):
    dealership = create_dealership(client, "Garage Nachtlauf AG")
    dealer_user = create_user(client, dealership)
    legacy = create_legacy_staff(client, dealership)
    staff = create_staff(client)

    found = _findings(db_session)

    assert uuid.UUID(legacy["id"]) in found
    assert uuid.UUID(staff["id"]) not in found
    assert uuid.UUID(dealer_user["id"]) not in found


def test_a_platform_admin_whose_tenant_names_no_betrieb_is_reported(client, db_session):
    staff = create_staff(client)
    user = db_session.get(User, uuid.UUID(staff["id"]))
    user.tenant_id = uuid.uuid4()  # a direct database fix gone wrong
    db_session.commit()

    assert _findings(db_session) == {user.id}


def test_nothing_is_reported_when_every_platform_admin_is_homed_in_the_platform_tenant(client, db_session):
    create_staff(client)
    create_user(client, create_dealership(client, "Garage Ruhig AG"))

    assert _findings(db_session) == set()

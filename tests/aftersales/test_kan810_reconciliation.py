"""KAN-810 (rule 10): Aftersales' nightly reconciliation watches every
three-column reference of the workshop order — clean for an order made
through the API, a finding for a reference that names nothing."""

import uuid

from sqlalchemy import update

from app.aftersales import reconciliation
from app.aftersales.models.service_order import ServiceOrder
from app.core.reconciliation import ReferenceCheck, find_orphans
from app.platform.organisation.models.labour_rate import LabourRateCategory
from tests.aftersales.service_order_support import add_job, add_position, labour_by_time, make_betrieb, new_order


def _findings(db) -> dict[str, list]:
    return {
        check.label: found
        for check in reconciliation.CHECKS
        if isinstance(check, ReferenceCheck) and (found := find_orphans(db, check))
    }


def test_an_order_made_through_the_api_has_no_finding(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    add_position(client, betrieb, order, order["jobs"][0]["id"], **labour_by_time(betrieb, "1"))
    assert _findings(db_session) == {}


def test_a_reference_naming_nothing_is_reported(client, db_session):
    betrieb = make_betrieb(client, db_session)
    order = add_job(client, betrieb, new_order(client, db_session, betrieb))
    add_position(client, betrieb, order, order["jobs"][0]["id"], **labour_by_time(betrieb, "1"))
    db_session.execute(
        update(ServiceOrder).values(contact_id=uuid.uuid4(), vehicle_id=uuid.uuid4(), location_id=uuid.uuid4())
    )
    db_session.commit()

    labels = set(_findings(db_session))

    assert labels == {
        "service_order.contact_id -> contact.id",
        "service_order.vehicle_id -> vehicle_mdm.id",
        "service_order.location_id -> location.id",
    }
    assert any(
        check.target_model is LabourRateCategory for check in reconciliation.CHECKS if isinstance(check, ReferenceCheck)
    )

"""KAN-716 (FR-A-45): offboarding needs an export produced after the
Betrieb's last write. A transaction begun before the export's snapshot and
committed after it stamps its rows with its own, earlier time — so its write
is not in the file, yet its stamp is before `produced_at`. The export now
records its snapshot (`export_job.produced_snapshot`), and offboarding
refuses while any row of the Betrieb was written by a transaction that
snapshot does not see.

On Postgres, with two sessions.
"""

import uuid

from sqlalchemy.orm import sessionmaker

from app.platform.models.export_job import ExportJob
from app.platform.organisation.models.dealership import Dealership
from tests.data_export_support import (
    betrieb_with_manager,
    export_store_fixture,  # noqa: F401 - registers the export_store fixture
    exported_and_acknowledged,
    staff,
)
from tests.test_offboarding import _offboard, _reason


def test_a_write_in_flight_when_the_snapshot_began_makes_the_export_out_of_date(client, export_store, engine, db_session):
    dealership_id, _, headers = betrieb_with_manager(client)
    writer = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        # Begun before the snapshot: the row is stamped now, committed later.
        row = writer.get(Dealership, uuid.UUID(dealership_id))
        row.trade_name = "Im Flug geschrieben"
        writer.flush()

        export = exported_and_acknowledged(client, engine, dealership_id, headers)

        writer.commit()
    finally:
        writer.close()

    db_session.expire_all()
    job = db_session.get(ExportJob, uuid.UUID(export["id"]))
    stamped = db_session.get(Dealership, uuid.UUID(dealership_id)).updated_at
    assert job.produced_snapshot is not None
    assert stamped < job.produced_at  # the timestamp check alone would pass it

    response = _offboard(client, dealership_id)

    assert response.status_code == 409, response.text
    assert _reason(response) == "export_out_of_date"
    state = client.get(f"/v1/platform/dealerships/{dealership_id}/offboarding", headers=staff()).json()
    assert state["refusal"] == "export_out_of_date"


def test_a_write_committed_before_the_snapshot_is_in_the_export_and_offboarding_goes_ahead(
    client, export_store, engine
):
    dealership_id, _, headers = betrieb_with_manager(client)
    writer = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        writer.get(Dealership, uuid.UUID(dealership_id)).trade_name = "Vor dem Export"
        writer.commit()
    finally:
        writer.close()

    exported_and_acknowledged(client, engine, dealership_id, headers)

    response = _offboard(client, dealership_id)

    assert response.status_code == 200, response.text


def test_a_write_after_the_export_is_still_refused(client, export_store, engine):
    dealership_id, _, headers = betrieb_with_manager(client)
    exported_and_acknowledged(client, engine, dealership_id, headers)
    writer = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        writer.get(Dealership, uuid.UUID(dealership_id)).trade_name = "Nach dem Export"
        writer.commit()
    finally:
        writer.close()

    assert _reason(_offboard(client, dealership_id)) == "export_out_of_date"

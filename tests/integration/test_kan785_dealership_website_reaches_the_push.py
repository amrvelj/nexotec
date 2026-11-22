"""KAN-785: a change of the Betrieb's website reaches the Google Business Profile push.

KAN-226 PR-3 pushes `websiteUri` from `dealership.website`, but only Platform's location facts
were consumed, so a new website reached Google only when one of that Betrieb's location facts
fired next. A dealership write that changes a published field now states
`platform.dealership.updated` in the same transaction (`organisation/services/dealership_facts.py`),
and `integration.business_profile_push_on_dealership_updated` pushes every bound location.

Against the recorded fixture only (`tests/gbp_fakes.py`, invented values): nothing here has run
against Google.
"""

import json
import uuid

from sqlalchemy import select

from app.core.outbox_model import OutboxMessage
from app.integration.consumers import handle_business_profile_push_on_dealership_message
from app.platform.organisation.services.dealership_facts import DEALERSHIP_UPDATED_EVENT
from tests.gbp_fakes import LOCATION_1, LOCATION_2
from tests.integration import test_kan226_business_profile_push as push
from tests.platform.test_kan203_business_master_data import _manager

google = push.google
client_connection = push.client_connection
betrieb = push.betrieb

CONSUMER = "integration.business_profile_push_on_dealership_updated"


def _facts(db_session, dealership_id) -> list[OutboxMessage]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.event_type == DEALERSHIP_UPDATED_EVENT, OutboxMessage.aggregate_id == dealership_id)
            .order_by(OutboxMessage.created_at)
        )
    )


def _patch_website(client, dealership, website):
    db_version = client.get(f"/v1/dealerships/{dealership.id}", headers=_manager(str(dealership.id))).json()["version"]
    return client.patch(
        f"/v1/dealerships/{dealership.id}",
        json={"website": website},
        headers={**_manager(str(dealership.id)), "If-Match": str(db_version)},
    )


def test_a_website_change_states_the_fact_with_the_row_and_an_unrelated_change_states_none(
    client, db_session, betrieb
):
    dealership, _location = betrieb

    changed = _patch_website(client, dealership, "www.neue-garage.example")
    assert changed.status_code == 200, changed.text
    [fact] = _facts(db_session, dealership.id)
    assert fact.tenant_id == dealership.id
    assert fact.payload == {
        "dealershipId": str(dealership.id),
        "version": changed.json()["version"],
        "changedFields": ["website"],
        "dealership": {"website": "www.neue-garage.example"},
    }

    same = _patch_website(client, dealership, "www.neue-garage.example")
    assert same.status_code == 200, same.text
    other = client.patch(
        f"/v1/dealerships/{dealership.id}",
        json={"email": "info@neue-garage.example"},
        headers={**_manager(str(dealership.id)), "If-Match": str(same.json()["version"])},
    )
    assert other.status_code == 200, other.text
    assert len(_facts(db_session, dealership.id)) == 1


def test_the_new_website_reaches_every_bound_location_once(
    client, db_session, engine, google, client_connection, betrieb
):
    fake, _ = google
    dealership, location = betrieb
    second = push._site(db_session, dealership.id, name="Winterthur")
    push._bind(db_session, google, dealership.id, location.id)
    push._bind(db_session, google, dealership.id, second.id, google_location=LOCATION_2)
    transport = push._transport(
        engine,
        consumer_name=CONSUMER,
        handler=handle_business_profile_push_on_dealership_message,
        event_type=DEALERSHIP_UPDATED_EVENT,
    )

    assert _patch_website(client, dealership, "www.neue-garage.example").status_code == 200
    [fact] = _facts(db_session, dealership.id)
    transport.deliver(fact)
    transport.deliver(fact)  # a redelivery: the processed-event row makes it a no-op

    patches = fake.patches()
    pushed_to = sorted(name for p in patches for name in (LOCATION_1, LOCATION_2) if str(p.url).split("?")[0].endswith(name))
    assert pushed_to == [LOCATION_1, LOCATION_2]
    for patch in patches:
        assert "websiteUri" in patch.url.params["updateMask"].split(",")
        assert json.loads(patch.content)["websiteUri"] == "https://www.neue-garage.example"


def test_a_fact_with_no_live_binding_sends_nothing(db_session, engine, google, client_connection, betrieb, client):
    fake, _ = google
    dealership, _location = betrieb
    transport = push._transport(
        engine,
        consumer_name=CONSUMER,
        handler=handle_business_profile_push_on_dealership_message,
        event_type=DEALERSHIP_UPDATED_EVENT,
    )

    assert _patch_website(client, dealership, f"www.{uuid.uuid4().hex[:6]}.example").status_code == 200
    transport.deliver(_facts(db_session, dealership.id)[0])

    assert fake.patches() == []

"""KAN-331, KAN-338: every request field that carries a typed VIN is
normalised and validated the way Stock's create is (KAN-244): upper-cased,
every whitespace character removed, then 17 characters of ISO 3779 (no I,
O or Q). One type does it, `app.core.validators.NormalisedVin`.

FR-V-15 — one VIN, one car: a lower-cased or spaced spelling of a VIN that
vehicle-mdm already holds is that car, never a second record (trade-in,
valuation) and never a miss that leaves the car unlinked (configuration). A
VIN still malformed after normalising is a 422 that writes nothing.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.auth import AccessRole, create_access_token
from app.sales.models.offer import SalesOffer
from app.sales.services.offer import create_offer
from app.valuation.models.valuation import Valuation
from app.vehicle.models.configuration import VehicleConfiguration
from app.vehicle.models.vehicle_mdm import VehicleMdm
from app.vehicle.public import create_or_get_vehicle_mdm
from tests.configuration_test_support import MANUAL_CODED_JSON, seed_manual_coded_lists

VIN = "WVWZZZ1KZAW331338"

SPELLINGS = [
    VIN.lower(),
    "WVW ZZZ 1KZ AW3 31338",
    " wvwzzz1kzaw331338 ",
    "WVWZZZ1KZ\tAW331338\n",
    "wVw ZZZ1KZAW331338",  # a no-break space pasted from a document
]

# Not a VIN, but shaped like a pre-1981 chassis number (1-17 letters and
# digits): KAN-353 lets a valuation and a configuration hold one, only with a
# first registration before 1981 — without it, still a 422 that writes nothing.
NOT_A_VIN = [
    "WVWZZZ1KZAW33133",  # 16 characters
    "WVWZZZ1KZAI331338",  # I is never in a VIN
    "WVWZZZ1KZAO331338",  # nor O
    "WVWZZZ1KZAQ331338",  # nor Q
]
# Neither a VIN nor a chassis number: refused by the type everywhere.
MALFORMED_SHAPE = [
    "WVWZZZ1KZAW3313388",  # 18 characters
    "WVWZZZ1KZAW33133-",
    "",
    "   ",
]
MALFORMED = NOT_A_VIN + MALFORMED_SHAPE


def _headers(tenant_id: uuid.UUID, group_id: uuid.UUID | None = None, **extra: str) -> dict[str, str]:
    token = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, group_id=group_id or uuid.uuid4(),
        roles=frozenset({AccessRole.SALES}), is_dealer_manager=False,
    )
    return {"Authorization": f"Bearer {token}", **extra}


def _known_car(db_session) -> VehicleMdm:
    vehicle, created = create_or_get_vehicle_mdm(db_session, vin=VIN, actor_id=uuid.uuid4())
    assert created
    db_session.commit()
    return vehicle


def _vehicle_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(VehicleMdm))


def _only_vin_refused(response) -> None:
    assert response.status_code == 422, response.text
    errors = response.json()["error"]["details"]["errors"]
    assert [e["loc"] for e in errors] == [["body", "vin"]]


# --- KAN-331: the trade-in of an offer -----------------------------------------


def _offer(db_session, tenant_id: uuid.UUID) -> SalesOffer:
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    db_session.commit()
    return offer


def _post_trade_in(client, offer: SalesOffer, tenant_id: uuid.UUID, vin: str):
    return client.post(
        f"/v1/sales/offers/{offer.id}/trade-in",
        json={"vin": vin, "vehicleLabel": "Volkswagen Golf"},
        headers=_headers(tenant_id, **{"If-Match": str(offer.version)}),
    )


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_trade_in_spelling_of_a_known_vin_is_the_known_car(client, db_session, spelling):
    vehicle = _known_car(db_session)
    tenant_id = uuid.uuid4()

    response = _post_trade_in(client, _offer(db_session, tenant_id), tenant_id, spelling)

    assert response.status_code == 200, response.text
    assert response.json()["tradeInVehicleId"] == str(vehicle.id)
    assert response.json()["tradeInVin"] == VIN
    assert _vehicle_count(db_session) == 1


@pytest.mark.parametrize("malformed", MALFORMED)
def test_a_malformed_trade_in_vin_is_422_and_writes_nothing(client, db_session, malformed):
    tenant_id = uuid.uuid4()
    offer = _offer(db_session, tenant_id)

    response = _post_trade_in(client, offer, tenant_id, malformed)

    _only_vin_refused(response)
    assert _vehicle_count(db_session) == 0
    db_session.expire_all()
    stored = db_session.get(SalesOffer, offer.id)
    assert stored.trade_in_vehicle_id is None and stored.trade_in_vin is None
    assert stored.version == offer.version


# --- KAN-331: a valuation --------------------------------------------------------


def _post_valuation(client, tenant_id: uuid.UUID, vin: str):
    return client.post(
        "/v1/valuations",
        json={"vin": vin, "finalOffer": "12500.00", "source": "manual"},
        headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())}),
    )


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_valuation_spelling_of_a_known_vin_is_the_known_car(client, db_session, spelling):
    vehicle = _known_car(db_session)

    response = _post_valuation(client, uuid.uuid4(), spelling)

    assert response.status_code == 201, response.text
    assert response.json()["vehicleId"] == str(vehicle.id)
    assert response.json()["vehicleVin"] == VIN
    assert _vehicle_count(db_session) == 1


@pytest.mark.parametrize("malformed", MALFORMED_SHAPE)
def test_a_malformed_valuation_vin_is_422_and_writes_nothing(client, db_session, malformed):
    response = _post_valuation(client, uuid.uuid4(), malformed)

    _only_vin_refused(response)
    assert _vehicle_count(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(Valuation)) == 0


def test_a_valuation_without_a_vin_still_creates_no_vehicle(client, db_session):
    """The VIN stays optional: a valuation needs no vehicle in the register."""

    response = client.post(
        "/v1/valuations",
        json={"finalOffer": Decimal("900.00").to_eng_string(), "source": "manual"},
        headers=_headers(uuid.uuid4()),
    )

    assert response.status_code == 201, response.text
    assert response.json()["vehicleId"] is None
    assert _vehicle_count(db_session) == 0


# --- KAN-338: the configuration --------------------------------------------------


def _create_configuration(client, tenant_id: uuid.UUID, **fields):
    return client.post(
        "/v1/configurations",
        json={"source": "manual", "mode": "record", "matchMethod": "manual", **MANUAL_CODED_JSON, **fields},
        headers=_headers(tenant_id, **{"Idempotency-Key": str(uuid.uuid4())}),
    )


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_configuration_created_with_a_spelling_of_a_known_vin_links_the_car(client, db_session, spelling):
    seed_manual_coded_lists(db_session)
    vehicle = _known_car(db_session)

    response = _create_configuration(client, uuid.uuid4(), vin=spelling)

    assert response.status_code == 201, response.text
    assert response.json()["vin"] == VIN
    assert response.json()["vehicleId"] == str(vehicle.id)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_configuration_patched_to_a_spelling_of_a_known_vin_links_the_car(client, db_session, spelling):
    seed_manual_coded_lists(db_session)
    vehicle = _known_car(db_session)
    tenant_id = uuid.uuid4()
    created = _create_configuration(client, tenant_id).json()
    assert created["vehicleId"] is None

    response = client.patch(
        f"/v1/configurations/{created['id']}",
        json={"vin": spelling},
        headers=_headers(tenant_id, **{"If-Match": str(created["version"])}),
    )

    assert response.status_code == 200, response.text
    assert response.json()["vin"] == VIN
    assert response.json()["vehicleId"] == str(vehicle.id)


@pytest.mark.parametrize("malformed", MALFORMED_SHAPE)
def test_a_malformed_configuration_vin_is_422_on_create_and_writes_nothing(client, db_session, malformed):
    seed_manual_coded_lists(db_session)

    response = _create_configuration(client, uuid.uuid4(), vin=malformed)

    _only_vin_refused(response)
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == 0


@pytest.mark.parametrize("malformed", MALFORMED_SHAPE)
def test_a_malformed_configuration_vin_is_422_on_patch_and_changes_nothing(client, db_session, malformed):
    seed_manual_coded_lists(db_session)
    vehicle = _known_car(db_session)
    tenant_id = uuid.uuid4()
    created = _create_configuration(client, tenant_id, vin=VIN).json()
    assert created["vehicleId"] == str(vehicle.id)

    response = client.patch(
        f"/v1/configurations/{created['id']}",
        json={"vin": malformed},
        headers=_headers(tenant_id, **{"If-Match": str(created["version"])}),
    )

    _only_vin_refused(response)
    db_session.expire_all()
    stored = db_session.get(VehicleConfiguration, uuid.UUID(created["id"]))
    assert stored.vin == VIN
    assert stored.vehicle_id == vehicle.id
    assert stored.version == created["version"]


def test_a_configuration_vin_can_still_be_cleared(client, db_session):
    """KAN-247: an explicit null clears a nullable observed field."""

    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _create_configuration(client, tenant_id, vin=VIN).json()

    response = client.patch(
        f"/v1/configurations/{created['id']}",
        json={"vin": None},
        headers=_headers(tenant_id, **{"If-Match": str(created["version"])}),
    )

    assert response.status_code == 200, response.text
    assert response.json()["vin"] is None


# --- one type, not three copies --------------------------------------------------


def test_every_typed_vin_field_uses_the_one_shared_type():
    """Stock and the trade-in take a VIN only. The valuation and the
    configuration take a VIN or, before 1981, a chassis number (KAN-353) —
    one shared type each, never a local copy."""

    from app.core.validators import NormalisedVin, NormalisedVinOrChassisNumber
    from app.inventory.schemas.stock_item import StockItemCreate
    from app.sales.schemas.offer import TradeInRequest
    from app.valuation.schemas.valuation import ValuationCreate
    from app.vehicle.schemas.configuration import ConfigurationCreate, ConfigurationUpdate

    for model in (StockItemCreate, TradeInRequest):
        assert model.model_fields["vin"].annotation == NormalisedVin | None, model.__name__
    for model in (ValuationCreate, ConfigurationCreate, ConfigurationUpdate):
        assert model.model_fields["vin"].annotation == NormalisedVinOrChassisNumber | None, model.__name__


# --- KAN-353: a pre-1981 chassis number on a valuation and a configuration -------

CHASSIS = "911 230 0123"  # a 1972 Porsche's chassis number, as typed
CHASSIS_STORED = "9112300123"


def _chassis_refused(response) -> None:
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["reason"] == "chassis_number_requires_pre_1981_registration"


def test_a_valuation_of_a_pre_1981_car_keeps_its_chassis_number_and_creates_no_vehicle(client, db_session):
    response = client.post(
        "/v1/valuations",
        json={"vin": CHASSIS, "vehicleFirstRegistration": "1972-05-01", "finalOffer": "85000.00", "source": "manual"},
        headers=_headers(uuid.uuid4(), **{"Idempotency-Key": str(uuid.uuid4())}),
    )

    assert response.status_code == 201, response.text
    assert response.json()["vehicleVin"] == CHASSIS_STORED
    assert response.json()["vehicleId"] is None
    assert _vehicle_count(db_session) == 0


@pytest.mark.parametrize("first_registration", [None, "1981-01-01", "1985-06-30"])
@pytest.mark.parametrize("not_a_vin", [CHASSIS, *NOT_A_VIN])
def test_a_valuation_chassis_number_without_a_pre_1981_registration_is_422(client, db_session, not_a_vin, first_registration):
    body = {"vin": not_a_vin, "finalOffer": "12500.00", "source": "manual"}
    if first_registration is not None:
        body["vehicleFirstRegistration"] = first_registration

    response = client.post(
        "/v1/valuations", json=body, headers=_headers(uuid.uuid4(), **{"Idempotency-Key": str(uuid.uuid4())})
    )

    _chassis_refused(response)
    assert _vehicle_count(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(Valuation)) == 0


def test_a_configuration_of_a_pre_1981_car_keeps_its_chassis_number_and_links_no_car(client, db_session):
    seed_manual_coded_lists(db_session)

    response = _create_configuration(client, uuid.uuid4(), vin=CHASSIS, firstRegistrationDate="1972-05-01")

    assert response.status_code == 201, response.text
    assert response.json()["vin"] == CHASSIS_STORED
    assert response.json()["vehicleId"] is None
    assert _vehicle_count(db_session) == 0


@pytest.mark.parametrize("not_a_vin", [CHASSIS, *NOT_A_VIN])
def test_a_configuration_chassis_number_without_a_pre_1981_registration_is_422(client, db_session, not_a_vin):
    seed_manual_coded_lists(db_session)

    response = _create_configuration(client, uuid.uuid4(), vin=not_a_vin, firstRegistrationDate="1990-01-01")

    _chassis_refused(response)
    assert db_session.scalar(select(func.count()).select_from(VehicleConfiguration)) == 0


def test_a_configuration_patch_judges_the_chassis_number_against_the_stored_registration(client, db_session):
    """On a PATCH the stored first registration counts: a chassis number is
    accepted onto a 1972 car, and moving that car's first registration to
    1990 while it holds the chassis number is refused, changing nothing."""

    seed_manual_coded_lists(db_session)
    tenant_id = uuid.uuid4()
    created = _create_configuration(client, tenant_id, firstRegistrationDate="1972-05-01").json()

    with_chassis = client.patch(
        f"/v1/configurations/{created['id']}",
        json={"vin": CHASSIS},
        headers=_headers(tenant_id, **{"If-Match": str(created["version"])}),
    )
    assert with_chassis.status_code == 200, with_chassis.text
    assert with_chassis.json()["vin"] == CHASSIS_STORED

    moved = client.patch(
        f"/v1/configurations/{created['id']}",
        json={"firstRegistrationDate": "1990-01-01"},
        headers=_headers(tenant_id, **{"If-Match": str(with_chassis.json()["version"])}),
    )
    _chassis_refused(moved)
    db_session.expire_all()
    stored = db_session.get(VehicleConfiguration, uuid.UUID(created["id"]))
    assert (stored.vin, stored.first_registration_date.isoformat()) == (CHASSIS_STORED, "1972-05-01")

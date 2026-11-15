"""KAN-123 step 5 (ADR-162): `app.customer.public` hands another context
frozen values — a `CustomerRef`, a `VehiclePartyRef` — never the mapped
`Customer` or `VehicleParty` row, and another group's customer still reads
as missing (rule 7, ADR-014).
"""

import dataclasses
import uuid

import pytest

from app.core.errors import NotFoundError
from app.customer import public
from app.customer.models.customer import Customer, CustomerLifecycleStatus, CustomerType, Language
from app.customer.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

GROUP_ID = uuid.uuid4()


def _customer(db_session, *, group_id=GROUP_ID, company: str | None = None) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.FR, first_name="Ada", last_name="Lovelace", company_name=company,
        credit_block=True, credit_block_reason="Mahnung offen",
    )
    db_session.add(customer)
    db_session.flush()
    return customer


def test_a_customer_is_handed_out_as_a_frozen_value(db_session):
    row = _customer(db_session)

    ref = public.get_customer_or_404(db_session, GROUP_ID, row.id)

    assert isinstance(ref, public.CustomerRef)
    assert not isinstance(ref, Customer)
    assert ref == public.CustomerRef(
        id=row.id, group_id=GROUP_ID, customer_number=row.customer_number, first_name="Ada", last_name="Lovelace",
        company_name=None, language=Language.FR, lifecycle_status=CustomerLifecycleStatus.PROSPECT,
        credit_block=True, credit_block_reason="Mahnung offen",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.credit_block = False  # type: ignore[misc]
    assert public.customer_display_name(ref) == "Ada Lovelace"
    assert public.customer_display_name(public.get_customer_or_404(db_session, GROUP_ID, _customer(db_session, company="Muster AG").id)) == "Muster AG"


def test_another_groups_customer_is_still_a_404(db_session):
    row = _customer(db_session, group_id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        public.get_customer_or_404(db_session, GROUP_ID, row.id)


def test_an_allocation_and_its_holders_are_handed_out_as_values(db_session):
    holder = _customer(db_session)
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123457", catalogue_variant_id=None)

    party = public.allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, customer_id=holder.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(), acting_dealership_id=None,
    )

    assert isinstance(party, public.VehiclePartyRef)
    assert not isinstance(party, VehicleParty)
    assert (party.vehicle_id, party.customer_id, party.role, party.effective_to) == (
        vehicle.id, holder.id, VehiclePartyRole.OWNER, None,
    )
    [(listed, name)] = public.list_vehicle_party_holders(db_session, vehicle_id=vehicle.id, group_id=GROUP_ID)
    assert listed == party
    assert name == "Ada Lovelace"
    assert public.list_vehicle_party_holders(db_session, vehicle_id=vehicle.id, group_id=uuid.uuid4()) == []


def test_customer_public_binds_no_mapped_class():
    assert not hasattr(public, "Customer")
    assert not hasattr(public, "VehicleParty")

"""KAN-123 step 5 (ADR-162): `app.contact.public` hands another context
frozen values — a `ContactRef`, a `VehiclePartyRef` — never the mapped
`Contact` or `VehicleParty` row, and another group's contact still reads
as missing (rule 7, ADR-014).
"""

import dataclasses
import uuid

import pytest

from app.contact import public
from app.contact.models.contact import Contact, ContactLifecycleStatus, ContactType, Language
from app.contact.models.vehicle_party import VehicleParty, VehiclePartyRole
from app.core.errors import NotFoundError
from app.vehicle.services.vehicle_mdm import create_vehicle_mdm

GROUP_ID = uuid.uuid4()


def _contact(db_session, *, group_id=GROUP_ID, company: str | None = None) -> Contact:
    contact = Contact(
        group_id=group_id, contact_number=f"K-{uuid.uuid4().hex[:6]}", contact_type=ContactType.INDIVIDUAL,
        language=Language.FR, first_name="Ada", last_name="Lovelace", company_name=company,
        credit_block=True, credit_block_reason="Mahnung offen",
    )
    db_session.add(contact)
    db_session.flush()
    return contact


def test_a_contact_is_handed_out_as_a_frozen_value(db_session):
    row = _contact(db_session)

    ref = public.get_contact_or_404(db_session, GROUP_ID, row.id)

    assert isinstance(ref, public.ContactRef)
    assert not isinstance(ref, Contact)
    assert ref == public.ContactRef(
        id=row.id, group_id=GROUP_ID, contact_number=row.contact_number, first_name="Ada", last_name="Lovelace",
        company_name=None, language=Language.FR, lifecycle_status=ContactLifecycleStatus.PROSPECT,
        credit_block=True, credit_block_reason="Mahnung offen", blocked=False, blocked_reason=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.credit_block = False  # type: ignore[misc]
    assert public.contact_display_name(ref) == "Ada Lovelace"
    assert public.contact_display_name(public.get_contact_or_404(db_session, GROUP_ID, _contact(db_session, company="Muster AG").id)) == "Muster AG"


def test_another_groups_contact_is_still_a_404(db_session):
    row = _contact(db_session, group_id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        public.get_contact_or_404(db_session, GROUP_ID, row.id)


def test_an_allocation_and_its_holders_are_handed_out_as_values(db_session):
    holder = _contact(db_session)
    vehicle = create_vehicle_mdm(db_session, vin="ZAR94000007123457", catalogue_variant_id=None)

    party = public.allocate_vehicle_party(
        db_session, vehicle_id=vehicle.id, contact_id=holder.id, role=VehiclePartyRole.OWNER,
        group_id=GROUP_ID, actor_id=uuid.uuid4(), acting_dealership_id=None,
    )

    assert isinstance(party, public.VehiclePartyRef)
    assert not isinstance(party, VehicleParty)
    assert (party.vehicle_id, party.contact_id, party.role, party.effective_to) == (
        vehicle.id, holder.id, VehiclePartyRole.OWNER, None,
    )
    [(listed, name)] = public.list_vehicle_party_holders(db_session, vehicle_id=vehicle.id, group_id=GROUP_ID)
    assert listed == party
    assert name == "Ada Lovelace"
    assert public.list_vehicle_party_holders(db_session, vehicle_id=vehicle.id, group_id=uuid.uuid4()) == []


def test_contact_public_binds_no_mapped_class():
    assert not hasattr(public, "Contact")
    assert not hasattr(public, "VehicleParty")

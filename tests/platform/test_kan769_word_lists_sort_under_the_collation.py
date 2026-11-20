"""KAN-769 (ADR-190): the platform's hand-built word lists sort under the collation — case
ignored, Ä/é beside A/e — not in the test database's byte order (`C.UTF-8`: «Zeta» before
«alpha», «Ärztin» after «Z»). Each expected order is the database's own
(`tests.text_sort_support.text_sort_order`)."""

import uuid

from app.core.auth import AccessRole, Principal
from app.platform.administration.services import configuration_copy, publishing_profile
from app.platform.identity.models.dealership_membership import DealershipMembership
from app.platform.identity.models.user import User, UserRole, UserStatus
from app.platform.identity.services.user_reads import list_active_users_acting_in, user_reads
from app.platform.organisation.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from app.platform.organisation.services.group_read_dealerships import list_group_read_dealerships
from app.platform.organisation.services.location import list_locations
from tests.dealership_support import valid_uid
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order

NAMES = ["Zeta", "alpha", "Ärztin", "Arzt", "école"]


def _collated(db_session, values: list[str]) -> list[str]:
    """The collated order, checked to differ from byte order — else the test proves nothing."""

    expected = text_sort_order(db_session, values)
    assert expected != sorted(values), "byte order and collated order must differ"
    return expected


def _group(db, *, group_read_enabled: bool = False) -> DealerGroup:
    group = DealerGroup(name=f"Group {uuid.uuid4().hex[:6]}", group_read_enabled=group_read_enabled)
    db.add(group)
    db.flush()
    return group


def _dealership(db, group: DealerGroup, legal_name: str = "Garage Test") -> Dealership:
    dealership = Dealership(
        dealer_group_id=group.id, legal_name=legal_name, franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    db.add(dealership)
    db.flush()
    return dealership


def _user(db, dealership: Dealership, *, first_name: str, last_name: str, is_dealer_manager: bool = False) -> User:
    user = User(
        tenant_id=dealership.id, first_name=first_name, last_name=last_name,
        email=f"u-{uuid.uuid4().hex[:8]}@example.ch", role=UserRole.SALES, access_roles=[],
        is_dealer_manager=is_dealer_manager, status=UserStatus.ACTIVE, auth_identity_id=str(uuid.uuid4()),
    )
    db.add(user)
    db.flush()
    return user


def _locations(db, dealership: Dealership, names: list[str], **fields) -> None:
    db.add_all(Location(tenant_id=dealership.id, name=name, **fields) for name in names)
    db.commit()


@POSTGRES_SQL
def test_kan769_users_acting_in_a_dealership_sort_by_last_then_first_name_under_the_collation(db_session):
    dealership = _dealership(db_session, _group(db_session))
    for last_name in NAMES:
        _user(db_session, dealership, first_name="Ada", last_name=last_name)
    first_names = ["zoé", "Ben", "Émile"]
    for first_name in first_names:
        _user(db_session, dealership, first_name=first_name, last_name="Müller")
    db_session.commit()

    users = list_active_users_acting_in(db_session, dealership_id=dealership.id)

    by_last = [user.last_name for user in users if user.last_name != "Müller"]
    assert by_last == _collated(db_session, NAMES)
    mullers = [user.first_name for user in users if user.last_name == "Müller"]
    assert mullers == _collated(db_session, first_names)
    last_names = [user.last_name for user in users]
    assert [name for name in last_names if name != "Müller"] == by_last
    assert last_names == text_sort_order(db_session, last_names)


@POSTGRES_SQL
def test_kan769_a_users_other_dealership_names_sort_under_the_collation(db_session):
    group = _group(db_session)
    home = _dealership(db_session, group)
    user = _user(db_session, home, first_name="Ada", last_name="Lovelace")
    for legal_name in NAMES:
        sister = _dealership(db_session, group, legal_name)
        db_session.add(DealershipMembership(user_id=user.id, dealership_id=sister.id))
    db_session.commit()

    (read,) = user_reads(db_session, [user], viewer_id=None, viewer_roles=frozenset({AccessRole.PLATFORM_ADMIN}))

    assert read.other_dealership_names == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_list_locations_sorts_by_name_under_the_collation(db_session):
    dealership = _dealership(db_session, _group(db_session))
    _locations(db_session, dealership, NAMES)

    names = [location.name for location in list_locations(db_session, dealership_id=dealership.id)]

    assert names == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_lead_routing_lists_active_then_inactive_locations_each_by_name_under_the_collation(db_session):
    dealership = _dealership(db_session, _group(db_session))
    inactive = ["zürich Nord", "Altstetten", "Örlikon"]
    _locations(db_session, dealership, NAMES, is_active=True)
    _locations(db_session, dealership, inactive, is_active=False)

    rows = publishing_profile.list_lead_routing(db_session, dealership.id)

    names = [location.name for location, _routing in rows]
    assert names == _collated(db_session, NAMES) + _collated(db_session, inactive)


@POSTGRES_SQL
def test_kan769_website_missing_lead_emails_name_the_active_locations_under_the_collation(db_session):
    dealership = _dealership(db_session, _group(db_session))
    _locations(db_session, dealership, NAMES, is_active=True)
    _locations(db_session, dealership, ["Abgemeldet"], is_active=False)

    items = publishing_profile.missing_items(db_session, dealership, "website")

    names = [item.location_name for item in items if item.code == "lead_email_missing"]
    assert names == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_a_groups_readable_dealerships_sort_by_legal_name_under_the_collation(db_session):
    group = _group(db_session, group_read_enabled=True)
    for legal_name in NAMES:
        _dealership(db_session, group, legal_name)
    db_session.commit()

    refs = list_group_read_dealerships(db_session, group_id=group.id)

    assert refs is not None
    assert [ref.label for ref in refs] == _collated(db_session, NAMES)


@POSTGRES_SQL
def test_kan769_configuration_copy_sources_sort_by_legal_name_under_the_collation(db_session):
    group = _group(db_session)
    target = _dealership(db_session, group, "Ziel AG")
    user = _user(db_session, target, first_name="Ada", last_name="Lovelace", is_dealer_manager=True)
    for legal_name in NAMES:
        sister = _dealership(db_session, group, legal_name)
        db_session.add(DealershipMembership(user_id=user.id, dealership_id=sister.id, is_dealer_manager=True))
    unmanaged = _dealership(db_session, group, "Aaa nicht verwaltet")
    db_session.add(DealershipMembership(user_id=user.id, dealership_id=unmanaged.id, is_dealer_manager=False))
    db_session.commit()
    principal = Principal(
        user_id=user.id, tenant_id=target.id, group_id=group.id, memberships=frozenset({target.id}),
        active_location_id=None, roles=frozenset(), is_dealer_manager=True,
    )

    options = configuration_copy.list_sources(db_session, principal, target=target)

    assert [option.legal_name for option in options] == _collated(db_session, NAMES)

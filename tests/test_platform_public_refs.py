"""KAN-123 step 7 (ADR-162): `app.platform.public` hands another context's
nightly reconciliation builders over `dealer_group`, `dealership`, `location`
and `user`, never the mapped classes — and each builder finds what the
check it replaced found, on Postgres.

The sources are `DemoWidget` (a test-only table with `tenant_id` and
`created_by`, any GUID column will do for a reference check) and
`Customer` (`group_id`, `advisor_id`, the columns Customers' own checks use).
"""

import uuid

from sqlalchemy import select

from app.core.reconciliation import StateCheck, find_orphans, find_rows_in_state
from app.customer.models.customer import Customer, CustomerType, Language
from app.platform import public
from app.platform.identity.models.user import User, UserRole, UserStatus
from app.platform.organisation.models.dealership import DealerGroup, Dealership, FranchiseType, Location
from tests.dealership_support import valid_uid
from tests.demo_models import DemoWidget


def _group(db) -> DealerGroup:
    group = DealerGroup(name=f"Group {uuid.uuid4().hex[:6]}")
    db.add(group)
    db.flush()
    return group


def _dealership(db, group: DealerGroup) -> Dealership:
    dealership = Dealership(
        dealer_group_id=group.id, legal_name="Garage Test", franchise_type=FranchiseType.INDEPENDENT,
        address_street="Bahnhofstrasse", address_house_number="1", address_postal_code="8001",
        address_locality="Zürich", address_canton="ZH", phone="+41441234567", uid=valid_uid(),
    )
    db.add(dealership)
    db.flush()
    return dealership


def _user(db, dealership: Dealership, status: UserStatus) -> User:
    user = User(
        tenant_id=dealership.id, first_name="Ada", last_name="Lovelace",
        email=f"ada-{uuid.uuid4().hex[:8]}@example.ch", role=UserRole.SALES, access_roles=[],
        is_dealer_manager=False, status=status, auth_identity_id=str(uuid.uuid4()),
    )
    db.add(user)
    db.flush()
    return user


def _widget(db, tenant_id: uuid.UUID, created_by: uuid.UUID | None = None) -> DemoWidget:
    widget = DemoWidget(tenant_id=tenant_id, name="w", created_by=created_by)
    db.add(widget)
    db.flush()
    return widget


def _customer(db, group_id: uuid.UUID, advisor_id: uuid.UUID | None = None) -> Customer:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}", customer_type=CustomerType.INDIVIDUAL,
        language=Language.EN, first_name="Ada", last_name="Lovelace", advisor_id=advisor_id,
    )
    db.add(customer)
    db.flush()
    return customer


def _widget_ids(db, where) -> set[uuid.UUID]:
    check = StateCheck(label="t", source_model=DemoWidget, source_row_id_column=DemoWidget.id, where=lambda: where)
    return {row_id for row_id, _ in find_rows_in_state(db, check)}


def test_platform_public_binds_no_organisation_or_user_class():
    for name in ("DealerGroup", "Dealership", "Location", "User"):
        assert not hasattr(public, name), name


def test_the_reference_checks_find_an_orphan_and_pass_a_valid_reference(db_session):
    group = _group(db_session)
    dealership = _dealership(db_session, group)
    location = Location(tenant_id=dealership.id, name="Hauptsitz")
    db_session.add(location)
    db_session.flush()
    user = _user(db_session, dealership, UserStatus.ACTIVE)
    valid = _widget(db_session, dealership.id, created_by=location.id)
    orphan = _widget(db_session, uuid.uuid4(), created_by=uuid.uuid4())
    advised = _customer(db_session, group.id, advisor_id=user.id)
    stray = _customer(db_session, uuid.uuid4(), advisor_id=uuid.uuid4())
    unadvised = _customer(db_session, group.id)
    db_session.flush()

    common = {"source_model": DemoWidget, "source_row_id_column": DemoWidget.id}
    dealership_check = public.dealership_reference_check(
        label="demo_widget.tenant_id -> dealership.id", source_fk_column=DemoWidget.tenant_id, **common
    )
    location_check = public.location_reference_check(
        label="demo_widget.created_by -> location.id", source_fk_column=DemoWidget.created_by, nullable=True, **common
    )
    customer = {"source_model": Customer, "source_row_id_column": Customer.id}
    group_check = public.dealer_group_reference_check(
        label="customer.group_id -> dealer_group.id", source_fk_column=Customer.group_id, **customer
    )
    user_check = public.user_reference_check(
        label="customer.advisor_id -> user.id", source_fk_column=Customer.advisor_id, nullable=True, **customer
    )

    assert find_orphans(db_session, dealership_check) == [(orphan.id, orphan.tenant_id)]
    assert find_orphans(db_session, location_check) == [(orphan.id, orphan.created_by)]
    assert find_orphans(db_session, group_check) == [(stray.id, stray.group_id)]
    # A null advisor is absent, not dangling (nullable=True); the valid one passes.
    assert find_orphans(db_session, user_check) == [(stray.id, stray.advisor_id)]
    assert {advised.id, unadvised.id}.isdisjoint({row for row, _ in find_orphans(db_session, user_check)})
    # source_where narrows the rows checked, as ReferenceCheck's own does.
    narrowed = public.dealership_reference_check(
        label="narrowed", source_fk_column=DemoWidget.tenant_id, source_where=lambda: DemoWidget.id == valid.id,
        **common,
    )
    assert find_orphans(db_session, narrowed) == []


def test_the_dealership_exists_builders_behave(db_session):
    group, other_group = _group(db_session), _group(db_session)
    dealership = _dealership(db_session, group)
    in_dealership = _widget(db_session, dealership.id, created_by=group.id)
    in_another_group = _widget(db_session, dealership.id, created_by=other_group.id)
    in_no_dealership = _widget(db_session, uuid.uuid4(), created_by=group.id)

    assert _widget_ids(db_session, public.dealership_exists(DemoWidget.tenant_id, correlate=DemoWidget)) == {
        in_dealership.id,
        in_another_group.id,
    }
    # created_by stands in for a group column here: the Betrieb's group is distinct from it.
    outside = public.dealership_outside_group(DemoWidget.tenant_id, DemoWidget.created_by, correlate=DemoWidget)
    assert _widget_ids(db_session, outside) == {in_another_group.id}
    # The scalar subquery: the Betrieb's group, NULL for an id naming none.
    group_of = public.dealership_group_of(DemoWidget.tenant_id, correlate=DemoWidget)
    rows = dict(db_session.execute(select(DemoWidget.id, group_of)).all())
    assert rows == {in_dealership.id: group.id, in_another_group.id: group.id, in_no_dealership.id: None}
    # Uncorrelated, the builders still compile on their own.
    assert db_session.scalar(select(public.dealership_exists(dealership.id))) is True
    assert db_session.scalar(select(public.dealership_exists(uuid.uuid4()))) is False


def test_user_is_deactivated_names_only_a_deactivated_user(db_session):
    group = _group(db_session)
    dealership = _dealership(db_session, group)
    active = _user(db_session, dealership, UserStatus.ACTIVE)
    gone = _user(db_session, dealership, UserStatus.DEACTIVATED)
    _widget(db_session, dealership.id, created_by=active.id)
    by_gone = _widget(db_session, dealership.id, created_by=gone.id)
    _widget(db_session, dealership.id, created_by=uuid.uuid4())
    _widget(db_session, dealership.id)

    assert _widget_ids(db_session, public.user_is_deactivated(DemoWidget.created_by, correlate=DemoWidget)) == {
        by_gone.id
    }


def test_the_plain_reads_hand_back_values(db_session):
    group = _group(db_session)
    dealership = _dealership(db_session, group)
    unknown = uuid.uuid4()

    assert public.find_dealer_group_ids(db_session, [group.id, dealership.id, unknown]) == [group.id]
    assert public.find_dealership_group_ids(db_session, [group.id, dealership.id, unknown]) == {
        dealership.id: group.id
    }

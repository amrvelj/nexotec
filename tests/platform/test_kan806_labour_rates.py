"""KAN-806 (D-A-19; rulings 177, 178, 183): labour rates — categories, rates
per location from a date, agreed rates, the question «which rate applies
here» (checks 11, 12, 15 of Aftersales slice 1) and the settings O16, O18,
O19 (check 49) with their real keys."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.audit_model import AuditEvent
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError
from app.core.money import PriceEntry
from app.platform import public as platform
from app.platform.organisation.models.dealership import Location
from app.platform.organisation.models.labour_rate import LabourRate, LabourRateAgreementKind
from app.platform.organisation.services import labour_rates as rates
from app.platform.organisation.services import setting_store as store
from app.platform.organisation.services.labour_rate_settings import (
    RATES_ENTERED_KEY,
    TIME_UNIT_KEY,
    WORK_UNITS_PER_HOUR_KEY,
)
from app.platform.organisation.services.setting_declarations import SettingValueError, declared_settings
from tests import setting_store_support as s
from tests.identity_support import create_dealership

TODAY = dt.date(2026, 11, 25)
TOMORROW = TODAY + dt.timedelta(days=1)
NEXT_MONTH = dt.date(2026, 12, 1)


@pytest.fixture()
def betrieb(client):
    return uuid.UUID(create_dealership(client))


def _seat(db, dealership_id) -> uuid.UUID:
    return db.scalar(select(Location.id).where(Location.tenant_id == dealership_id))


def _set(db, dealership_id, key, value, location_id=None):
    version = store.get_slot(db, dealership_id=dealership_id, location_id=location_id, key=key).version
    store.change_setting(
        db, dealership_id=dealership_id, location_id=location_id, key=key, value=value,
        expected_version=version, actor_id=None,
    )
    db.commit()


def _category(db, dealership_id, name="Mechanik"):
    category = rates.create_category(db, dealership_id=dealership_id, name=name, name_translations=None, actor_id=None)
    db.commit()
    return category


def _rate(db, dealership_id, category, *, amount="150.00", valid_from=TODAY, location_id=None, entered=PriceEntry.NET):
    rate = rates.create_rate(
        db, dealership_id=dealership_id, category_id=category.id, location_id=location_id or _seat(db, dealership_id),
        valid_from=valid_from, amount=Decimal(amount), entered=entered, actor_id=None, on=TODAY,
    )
    db.commit()
    return rate


def _agreement(db, dealership_id, category, contact_id, *, amount="120.00", valid_from=TODAY,
               kind=LabourRateAgreementKind.RATE, entered=PriceEntry.NET):
    row = rates.create_agreement(
        db, dealership_id=dealership_id, contact=rates.AgreementContact(id=contact_id, label="Helvetia Versicherungen"),
        category_id=category.id, kind=kind, valid_from=valid_from,
        amount=Decimal(amount) if kind is LabourRateAgreementKind.RATE else None,
        entered=entered if kind is LabourRateAgreementKind.RATE else None, actor_id=None, on=TODAY,
    )
    db.commit()
    return row


def _answer(db, dealership_id, category, *, on=TODAY, recipient=None, location_id=None):
    return platform.labour_rate_for(
        db, dealership_id=dealership_id, category_id=category.id, location_id=location_id or _seat(db, dealership_id),
        on=on, invoice_recipient_contact_id=recipient,
    )


def _reason(caught) -> str:
    return caught.value.details["reason"]


@pytest.fixture()
def net_betrieb(db_session, betrieb):
    _set(db_session, betrieb, RATES_ENTERED_KEY, "net")
    return betrieb


# --- the declarations --------------------------------------------------------------------


def test_the_three_labour_rate_settings_are_declared_for_aftersales():
    declared = {d.key: d for d in declared_settings()}
    assert {RATES_ENTERED_KEY, TIME_UNIT_KEY, WORK_UNITS_PER_HOUR_KEY} <= set(declared)
    o16 = declared[RATES_ENTERED_KEY]
    assert (o16.module, o16.choices, o16.when_not_set, o16.checklist_item) == ("aftersales", ("net", "gross"), "refuses", True)
    o18 = declared[TIME_UNIT_KEY]
    assert (o18.acts_as, o18.choice_requires) == ("hours", (("work_units", WORK_UNITS_PER_HOUR_KEY),))


# --- O16: no rate without it (ruling 177) --------------------------------------------------


def test_no_rate_is_entered_while_o16_is_not_set(db_session, betrieb):
    category = _category(db_session, betrieb)
    with pytest.raises(ConflictError) as caught:
        _rate(db_session, betrieb, category)
    assert _reason(caught) == "labour_rates_entry_basis_not_set"
    assert caught.value.details["setting"] == RATES_ENTERED_KEY


def test_a_rate_typed_otherwise_than_o16_is_refused_never_relabelled(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    with pytest.raises(ConflictError) as caught:
        _rate(db_session, net_betrieb, category, entered=PriceEntry.GROSS)
    assert (_reason(caught), caught.value.details["entered"]) == ("labour_rate_entry_basis_changed", "net")


def test_o16_counts_at_the_rates_location(db_session, net_betrieb):
    north = s.add_location(db_session, net_betrieb)
    _set(db_session, net_betrieb, RATES_ENTERED_KEY, "gross", location_id=north)
    category = _category(db_session, net_betrieb)
    rate = _rate(db_session, net_betrieb, category, location_id=north, amount="162.15", entered=PriceEntry.GROSS)
    assert (rate.amount, rate.entered) == (Decimal("162.15"), PriceEntry.GROSS)


def test_check_12_a_rate_keeps_how_it_was_typed_when_o16_changes(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category, amount="150.0000")
    _set(db_session, net_betrieb, RATES_ENTERED_KEY, "gross")
    answer = _answer(db_session, net_betrieb, category)
    assert (answer.amount, answer.entered) == (Decimal("150.0000"), PriceEntry.NET)


# --- ruling 178: a rate in use is never changed (check 15) --------------------------------


def test_a_rate_starting_yesterday_is_refused(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    with pytest.raises(UnprocessableEntityError) as caught:
        _rate(db_session, net_betrieb, category, valid_from=TODAY - dt.timedelta(days=1))
    assert (_reason(caught), caught.value.details["today"]) == ("labour_rate_starts_in_the_past", TODAY.isoformat())


def test_check_15_a_new_rate_starts_on_a_date_and_earlier_days_keep_theirs(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    first = _rate(db_session, net_betrieb, category, amount="150.00")
    second = _rate(db_session, net_betrieb, category, amount="160.00", valid_from=NEXT_MONTH)
    assert _answer(db_session, net_betrieb, category).rate_id == first.id
    assert _answer(db_session, net_betrieb, category, on=NEXT_MONTH - dt.timedelta(days=1)).amount == Decimal("150.00")
    assert _answer(db_session, net_betrieb, category, on=NEXT_MONTH).rate_id == second.id
    statuses = rates.rate_statuses(rates.rates_of(db_session, dealership_id=net_betrieb), NEXT_MONTH)
    assert (statuses[first.id], statuses[second.id]) == ("superseded", "in_force")


def test_check_15_a_rate_in_use_cannot_be_withdrawn_even_on_its_first_day(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    rate = _rate(db_session, net_betrieb, category)
    with pytest.raises(ConflictError) as caught:
        rates.withdraw_rate(db_session, dealership_id=net_betrieb, rate_id=rate.id, expected_version=rate.version,
                            actor_id=None, on=TODAY)
    assert _reason(caught) == "labour_rate_in_use"


def test_a_rate_still_to_come_can_be_withdrawn_once_and_is_kept(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    rate = _rate(db_session, net_betrieb, category, valid_from=TOMORROW)
    withdrawn = rates.withdraw_rate(db_session, dealership_id=net_betrieb, rate_id=rate.id,
                                    expected_version=rate.version, actor_id=None, on=TODAY)
    db_session.commit()
    assert withdrawn.withdrawn_at is not None and withdrawn.version == 2
    assert db_session.get(LabourRate, rate.id) is not None
    assert _answer(db_session, net_betrieb, category, on=TOMORROW).found is False
    with pytest.raises(ConflictError) as caught:
        rates.withdraw_rate(db_session, dealership_id=net_betrieb, rate_id=rate.id, expected_version=2,
                            actor_id=None, on=TODAY)
    assert _reason(caught) == "labour_rate_already_withdrawn"
    # The date is free again.
    assert _rate(db_session, net_betrieb, category, valid_from=TOMORROW, amount="155.00").amount == Decimal("155.00")


def test_two_rates_of_one_category_location_and_date_are_refused(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category)
    with pytest.raises(ConflictError) as caught:
        _rate(db_session, net_betrieb, category, amount="151.00")
    assert _reason(caught) == "labour_rate_exists_on_date"


def test_a_rate_has_at_most_four_decimals_and_is_above_zero(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    assert _rate(db_session, net_betrieb, category, amount="123.4567").amount == Decimal("123.4567")
    for amount in ("0", "-1", "1.23456"):
        with pytest.raises(UnprocessableEntityError) as caught:
            _rate(db_session, net_betrieb, category, amount=amount, valid_from=TOMORROW)
        db_session.rollback()
        assert _reason(caught) == "labour_rate_amount_invalid", amount


def test_a_deactivated_category_or_location_gets_no_new_rate(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    rates.change_category(db_session, dealership_id=net_betrieb, category_id=category.id,
                          expected_version=category.version, actor_id=None, is_active=False)
    db_session.commit()
    with pytest.raises(ConflictError) as caught:
        _rate(db_session, net_betrieb, category)
    assert _reason(caught) == "labour_rate_category_inactive"
    db_session.rollback()
    other = _category(db_session, net_betrieb, "Karosserie")
    north = s.add_location(db_session, net_betrieb, active=False)
    with pytest.raises(ConflictError) as caught:
        _rate(db_session, net_betrieb, other, location_id=north)
    assert _reason(caught) == "labour_rate_location_inactive"


def test_another_betriebs_location_is_not_found(client, db_session, net_betrieb):
    elsewhere = uuid.UUID(create_dealership(client))
    category = _category(db_session, net_betrieb)
    with pytest.raises(NotFoundError):
        _rate(db_session, net_betrieb, category, location_id=_seat(db_session, elsewhere))


# --- categories ---------------------------------------------------------------------------


def test_a_category_name_is_unique_in_the_betrieb_whatever_its_case(db_session, net_betrieb):
    _category(db_session, net_betrieb, "Mechanik")
    with pytest.raises(ConflictError) as caught:
        _category(db_session, net_betrieb, "  mechanik ")
    assert _reason(caught) == "labour_rate_category_name_taken"


def test_a_category_keeps_only_the_translations_given(db_session, net_betrieb):
    category = rates.create_category(
        db_session, dealership_id=net_betrieb, name="Lack", name_translations={"fr": "Peinture", "it": " "},
        actor_id=None,
    )
    assert category.name_translations == {"fr": "Peinture"}
    with pytest.raises(UnprocessableEntityError):
        rates.create_category(db_session, dealership_id=net_betrieb, name="Diagnose", name_translations={"es": "x"},
                              actor_id=None)


def test_every_write_is_audited(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    rate = _rate(db_session, net_betrieb, category, valid_from=TOMORROW)
    rates.withdraw_rate(db_session, dealership_id=net_betrieb, rate_id=rate.id, expected_version=1, actor_id=None, on=TODAY)
    db_session.commit()
    actions = db_session.execute(
        select(AuditEvent.entity_type, AuditEvent.action)
        .where(AuditEvent.tenant_id == net_betrieb, AuditEvent.entity_type.like("labour_rate%"))
        .order_by(AuditEvent.created_at)
    ).all()
    assert [tuple(row) for row in actions] == [
        ("labour_rate_category", "create"), ("labour_rate", "create"), ("labour_rate", "withdraw"),
    ]


# --- check 11: which rate applies here ----------------------------------------------------


def test_check_11_the_category_rate_at_the_location_on_the_day(db_session, net_betrieb):
    north = s.add_location(db_session, net_betrieb)
    category = _category(db_session, net_betrieb)
    seat_rate = _rate(db_session, net_betrieb, category, amount="150.00")
    _rate(db_session, net_betrieb, category, amount="140.00", location_id=north)
    answer = _answer(db_session, net_betrieb, category)
    assert (answer.found, answer.source, answer.rate_id, answer.amount) == (
        True, "category_rate", seat_rate.id, Decimal("150.00"),
    )
    assert _answer(db_session, net_betrieb, category, location_id=north).amount == Decimal("140.00")


def test_check_11_a_rate_agreed_with_the_invoice_recipient_comes_first(db_session, net_betrieb):
    insurer = uuid.uuid4()
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category, amount="150.00")
    agreement = _agreement(db_session, net_betrieb, category, insurer, amount="120.00")
    agreed = _answer(db_session, net_betrieb, category, recipient=insurer)
    assert (agreed.source, agreed.rate_id, agreed.amount) == ("agreement", agreement.id, Decimal("120.00"))
    # Somebody else pays: the category rate.
    assert _answer(db_session, net_betrieb, category, recipient=uuid.uuid4()).source == "category_rate"
    assert _answer(db_session, net_betrieb, category).source == "category_rate"


def test_an_ended_agreement_gives_the_category_rate_back(db_session, net_betrieb):
    insurer = uuid.uuid4()
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category, amount="150.00")
    _agreement(db_session, net_betrieb, category, insurer, amount="120.00")
    _agreement(db_session, net_betrieb, category, insurer, valid_from=NEXT_MONTH, kind=LabourRateAgreementKind.END)
    assert _answer(db_session, net_betrieb, category, recipient=insurer).source == "agreement"
    assert _answer(db_session, net_betrieb, category, recipient=insurer, on=NEXT_MONTH).source == "category_rate"


def test_an_end_with_nothing_to_end_is_refused(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    with pytest.raises(ConflictError) as caught:
        _agreement(db_session, net_betrieb, category, uuid.uuid4(), kind=LabourRateAgreementKind.END)
    assert _reason(caught) == "labour_rate_agreement_nothing_to_end"


def test_an_agreement_follows_o16_at_the_betrieb(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    with pytest.raises(ConflictError) as caught:
        _agreement(db_session, net_betrieb, category, uuid.uuid4(), entered=PriceEntry.GROSS)
    assert _reason(caught) == "labour_rate_entry_basis_changed"


def test_an_agreement_in_use_cannot_be_withdrawn(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    row = _agreement(db_session, net_betrieb, category, uuid.uuid4())
    with pytest.raises(ConflictError) as caught:
        rates.withdraw_agreement(db_session, dealership_id=net_betrieb, agreement_id=row.id,
                                 expected_version=row.version, actor_id=None, on=TODAY)
    assert _reason(caught) == "labour_rate_agreement_in_use"


def test_no_rate_found_is_an_answer_not_an_error(client, db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    answer = _answer(db_session, net_betrieb, category)
    assert (answer.found, answer.missing, answer.amount, answer.source) == (False, "no_rate", None, None)
    elsewhere = uuid.UUID(create_dealership(client))
    foreign = platform.labour_rate_for(
        db_session, dealership_id=elsewhere, category_id=category.id, location_id=_seat(db_session, elsewhere),
        on=TODAY, invoice_recipient_contact_id=None,
    )
    assert (foreign.found, foreign.missing) == (False, "unknown_category")


def test_a_deactivated_category_still_answers_for_its_days(db_session, net_betrieb):
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category)
    rates.change_category(db_session, dealership_id=net_betrieb, category_id=category.id,
                          expected_version=category.version, actor_id=None, is_active=False)
    db_session.commit()
    answer = _answer(db_session, net_betrieb, category)
    assert (answer.found, answer.category_is_active) == (True, False)


# --- O18 and O19: hours or work units (ruling 183, check 49) ------------------------------


def test_check_49_work_units_need_work_units_per_hour(db_session, betrieb):
    with pytest.raises(SettingValueError) as caught:
        _set(db_session, betrieb, TIME_UNIT_KEY, "work_units")
    assert caught.value.reason == "setting_choice_requires_setting"
    db_session.rollback()
    _set(db_session, betrieb, WORK_UNITS_PER_HOUR_KEY, Decimal(12))
    _set(db_session, betrieb, TIME_UNIT_KEY, "work_units")
    unit = platform.labour_time_unit(db_session, dealership_id=betrieb, location_id=None)
    assert (unit.unit, unit.work_units_per_hour) == ("work_units", Decimal(12))


def test_not_set_is_hours(db_session, betrieb):
    unit = platform.labour_time_unit(db_session, dealership_id=betrieb, location_id=_seat(db_session, betrieb))
    assert (unit.unit, unit.work_units_per_hour, unit.unit_is_set) == ("hours", None, False)


def test_work_units_per_hour_is_a_whole_number_from_1_to_100(db_session, betrieb):
    for value, reason in ((Decimal(0), "setting_value_below_minimum"), (Decimal(101), "setting_value_above_maximum"),
                          (Decimal("12.5"), "setting_value_too_many_decimals")):
        with pytest.raises(SettingValueError) as caught:
            _set(db_session, betrieb, WORK_UNITS_PER_HOUR_KEY, value)
        db_session.rollback()
        assert caught.value.reason == reason


def test_the_rate_stays_per_hour_whatever_the_unit(db_session, net_betrieb):
    _set(db_session, net_betrieb, WORK_UNITS_PER_HOUR_KEY, Decimal(12))
    _set(db_session, net_betrieb, TIME_UNIT_KEY, "work_units")
    category = _category(db_session, net_betrieb)
    _rate(db_session, net_betrieb, category, amount="100.00")
    answer = _answer(db_session, net_betrieb, category)
    assert answer.amount == Decimal("100.00")  # per hour; 9 AW are CHF 75.00 in app.core.money (check 47)

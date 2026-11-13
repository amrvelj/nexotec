"""KAN-123 step 2 (ADR-162): `app.valuation.public` hands another context a
frozen `ValuationSummary`, never the mapped `Valuation` row — the reads Sales'
trade-in and Stock's `valuationRef` use, and the compensation's answer — and
another Betrieb's valuation still reads as missing (rule 7, ADR-029).
"""

import dataclasses
import uuid
from decimal import Decimal

import pytest

from app.core.errors import NotFoundError
from app.valuation import public
from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.schemas.valuation import ValuationCreate
from app.valuation.services.valuation import create_valuation, mark_used

_VIN = "WVWZZZ1KZAW123123"


def _valuation(db_session, tenant_id: uuid.UUID) -> Valuation:
    return create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(
            vin=_VIN, vehicle_make="VW", vehicle_model="Golf", vehicle_trim="Life",
            source=ValuationSource.MANUAL, final_offer=Decimal("12000.00"), is_draft=False,
        ),
        actor_id=uuid.uuid4(),
    )


def _assert_is_the_summary_of(summary: object, row: Valuation) -> None:
    assert isinstance(summary, public.ValuationSummary)
    assert not isinstance(summary, Valuation)
    assert summary == public.ValuationSummary(
        id=row.id, tenant_id=row.tenant_id, valuation_number=row.valuation_number,
        vehicle_id=row.vehicle_id, vehicle_vin=row.vehicle_vin, vehicle_make="VW", vehicle_model="Golf",
        vehicle_trim="Life", configuration_id=None, configuration_label=None,
        final_offer=Decimal("12000.00"), source=ValuationSource.MANUAL, created_at=row.created_at,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.final_offer = Decimal(1)  # type: ignore[misc]


def test_get_valuation_answers_a_frozen_summary_not_the_row(db_session):
    tenant_id = uuid.uuid4()
    row = _valuation(db_session, tenant_id)

    _assert_is_the_summary_of(public.get_valuation_or_404(db_session, tenant_id, row.id), row)


def test_another_betriebs_valuation_reads_as_missing(db_session):
    row = _valuation(db_session, uuid.uuid4())

    with pytest.raises(NotFoundError):
        public.get_valuation_or_404(db_session, uuid.uuid4(), row.id)
    assert public.list_valid_valuations_for_vin(db_session, tenant_id=uuid.uuid4(), vin=_VIN) == []


def test_the_valid_lists_answer_summaries(db_session):
    tenant_id = uuid.uuid4()
    row = _valuation(db_session, tenant_id)

    (by_vin,) = public.list_valid_valuations_for_vin(db_session, tenant_id=tenant_id, vin=_VIN)
    _assert_is_the_summary_of(by_vin, row)
    assert public.list_valid_valuations_for_vehicle(db_session, tenant_id=tenant_id, vehicle_id=uuid.uuid4()) == []


def test_revert_valuation_use_answers_a_summary_of_the_cleared_row(db_session):
    tenant_id = uuid.uuid4()
    row = _valuation(db_session, tenant_id)
    mark_used(db_session, valuation=row, actor_id=None)
    assert row.used_at is not None

    reverted = public.revert_valuation_use(db_session, tenant_id=tenant_id, valuation_id=row.id, actor_id=None)

    db_session.refresh(row)
    assert row.used_at is None
    _assert_is_the_summary_of(reverted, row)

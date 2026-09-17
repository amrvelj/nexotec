"""WP-8 PR-5: the standalone valuation application (ADR-066/ADR-048 as
amended, FR-V-09/FR-V-17)."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.core.auth import AccessRole, create_access_token
from app.core.base import utcnow
from app.core.errors import ConflictError
from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.valuation.models.valuation import Valuation, ValuationSource
from app.valuation.schemas.valuation import DEFAULT_VALIDITY_DAYS, DeductionInput, ValuationCreate
from app.valuation.services.valuation import (
    allocate_valuation_number,
    create_valuation,
    derive_status,
    get_deductions,
    list_valid_valuations_for_vehicle,
    list_valuations,
    mark_used,
)


def test_valuation_number_increments_per_tenant(db_session):
    tenant_id = uuid.uuid4()
    assert allocate_valuation_number(db_session, tenant_id) == "B-000001"
    assert allocate_valuation_number(db_session, tenant_id) == "B-000002"


def test_default_validity_is_30_days():
    """Q-11 — confirmed live on the reference prototype's own create
    dialog default. Product-confirmed via the UI, not independently
    re-derived by engineering."""

    assert DEFAULT_VALIDITY_DAYS == 30


def test_creatable_with_no_customer_no_vehicle(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.AUTO_I_DAT, provider_value=Decimal(19000), final_offer=Decimal(19000)),
        actor_id=uuid.uuid4(),
    )

    assert valuation.vehicle_id is None
    assert valuation.customer_id is None
    assert valuation.valuation_number.startswith("B-")
    assert derive_status(valuation) == "valid"


def test_creating_with_a_vin_resolves_or_creates_the_real_vehicle_mdm(db_session):
    """"Ist das Fahrzeug nicht erfasst, wird es mit der Bewertung angelegt
    — ein Schritt, nicht zwei." (confirmed live)."""

    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(
            vin="WBA4Y9F55LCE89GLA", source=ValuationSource.MANUAL, final_offer=Decimal(16350),
        ),
        actor_id=uuid.uuid4(),
    )
    assert valuation.vehicle_id is not None
    assert valuation.vehicle_vin == "WBA4Y9F55LCE89GLA"

    # Creating a second valuation with the SAME vin resolves to the same
    # vehicle-mdm record (FR-V-15's own "not a validation error" rule).
    second = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(vin="WBA4Y9F55LCE89GLA", source=ValuationSource.MANUAL, final_offer=Decimal(16000)),
        actor_id=uuid.uuid4(),
    )
    assert second.vehicle_id == valuation.vehicle_id


def test_deductions_are_stored_as_child_rows(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(
            source=ValuationSource.AUTO_I_DAT,
            provider_value=Decimal(27850),
            deductions=[
                DeductionInput(label="MFK-Vorbereitung", amount=Decimal(570)),
                DeductionInput(label="Bremsen vorne", amount=Decimal(550)),
            ],
            final_offer=Decimal(26750),
        ),
        actor_id=uuid.uuid4(),
    )
    deductions = get_deductions(db_session, valuation.id)
    assert [d.label for d in deductions] == ["MFK-Vorbereitung", "Bremsen vorne"]
    assert [d.amount for d in deductions] == [Decimal(570), Decimal(550)]


def test_final_offer_may_differ_from_provider_value_minus_deductions(db_session):
    """Confirmed live: "Das Eintauschangebot darf vom Nettowert abweichen
    — das ist die Verhandlung.\""""

    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(
            source=ValuationSource.AUTO_I_DAT,
            provider_value=Decimal(27850),
            deductions=[DeductionInput(label="MFK", amount=Decimal(570))],
            final_offer=Decimal(26750),  # not 27850-570=27280 — a real negotiated number
        ),
        actor_id=uuid.uuid4(),
    )
    assert valuation.final_offer == Decimal(26750)


def test_status_derivation_never_stored(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(10000)), actor_id=uuid.uuid4(),
    )
    assert derive_status(valuation) == "valid"

    # Force it into the past directly on the row — no job runs to "expire"
    # it; the very next read must already see it as expired.
    valuation.valid_until = utcnow() - dt.timedelta(days=1)
    db_session.commit()
    assert derive_status(valuation) == "expired"


def test_draft_is_never_counted_as_valid_regardless_of_its_own_validity(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(10000), is_draft=True),
        actor_id=uuid.uuid4(),
    )
    assert derive_status(valuation) == "draft"


def test_mark_used_is_terminal_and_idempotent(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(10000)), actor_id=uuid.uuid4(),
    )
    used = mark_used(db_session, valuation=valuation, actor_id=uuid.uuid4())
    assert derive_status(used) == "used"

    # Idempotent — a retried contract-confirmation call must not error or
    # double-fire the event.
    again = mark_used(db_session, valuation=used, actor_id=uuid.uuid4())
    assert again.used_at == used.used_at


def test_mark_used_refuses_an_already_expired_valuation(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(10000)), actor_id=uuid.uuid4(),
    )
    valuation.valid_until = utcnow() - dt.timedelta(days=1)
    db_session.commit()

    with pytest.raises(ConflictError):
        mark_used(db_session, valuation=valuation, actor_id=uuid.uuid4())


def test_list_valid_valuations_for_vehicle_excludes_expired_and_draft(db_session):
    tenant_id = uuid.uuid4()
    valuation = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(vin="1HGCM82633A004352", source=ValuationSource.MANUAL, final_offer=Decimal(5000)),
        actor_id=uuid.uuid4(),
    )
    expired = create_valuation(
        db_session,
        tenant_id=tenant_id,
        group_id=uuid.uuid4(),
        data=ValuationCreate(vin="1HGCM82633A004352", source=ValuationSource.MANUAL, final_offer=Decimal(4800)),
        actor_id=uuid.uuid4(),
    )
    expired.valid_until = utcnow() - dt.timedelta(days=1)
    db_session.commit()

    results = list_valid_valuations_for_vehicle(db_session, tenant_id=tenant_id, vehicle_id=valuation.vehicle_id)
    assert [v.id for v in results] == [valuation.id]


def test_unattached_chip_matches_ohne_kunde(db_session):
    tenant_id = uuid.uuid4()
    create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(1000)), actor_id=uuid.uuid4(),
    )
    sort_fields = [SortField(api_name="createdAt", column=Valuation.created_at, direction="desc", nullable=False)]
    params = SortPageParams(limit=50, cursor=None, sort_fields=sort_fields)
    rows, _cursor, total, _est = list_valuations(
        db_session, tenant_id=tenant_id, chip="unattached", q=None, created_by=None, params=params
    )
    assert total == 1
    assert rows[0].customer_id is None


def test_status_is_not_in_the_sort_allow_list():
    from app.valuation.api.valuations import VALUATION_SORT_FIELDS

    assert "status" not in VALUATION_SORT_FIELDS


def _sales_token() -> str:
    tenant_id = uuid.uuid4()
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_valuation_over_http_returns_a_derived_status(client):
    """KAN-65 — every request used to 500: `_valuation_read` built
    `ValuationRead` by validating straight off the `Valuation` ORM object,
    but `status` is derived (ADR-066) and is not a column, so pydantic
    raised a ValidationError for the missing required field before the
    derived value was ever attached. Every existing test called the
    service layer directly and never touched this response path, so this
    went unnoticed. This test goes through the real endpoint precisely to
    close that gap.
    """

    response = client.post(
        "/v1/valuations",
        json={"finalOffer": "1000.00", "source": "manual", "validForDays": 30},
        headers=_bearer(_sales_token()),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "valid"
    assert body["finalOffer"] == "1000.00"
    assert body["deductions"] == []


def test_list_valuations_over_http_returns_a_derived_status_per_row(client):
    token = _sales_token()
    create = client.post(
        "/v1/valuations",
        json={"finalOffer": "500.00", "source": "manual", "validForDays": 30},
        headers=_bearer(token),
    )
    assert create.status_code == 201, create.text

    response = client.get("/v1/valuations", headers=_bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "valid"

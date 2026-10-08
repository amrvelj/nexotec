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
from app.sales.models.contract import ContractStatus, SalesContract
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
    mark_used_by_hand,
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


def _manual_valuation(db_session, tenant_id: uuid.UUID, *, expired: bool = False) -> Valuation:
    valuation = create_valuation(
        db_session, tenant_id=tenant_id, group_id=uuid.uuid4(),
        data=ValuationCreate(source=ValuationSource.MANUAL, final_offer=Decimal(10000)), actor_id=uuid.uuid4(),
    )
    if expired:
        valuation.valid_until = utcnow() - dt.timedelta(days=1)
        db_session.commit()
    return valuation


def _contract_carrying(
    db_session, valuation_id: uuid.UUID, *, tenant_id: uuid.UUID, signed: bool,
    status: ContractStatus = ContractStatus.CONFIRMED,
) -> SalesContract:
    contract = SalesContract(
        tenant_id=tenant_id, contract_number=f"C-{uuid.uuid4().hex[:6]}", status=status,
        trade_in_valuation_id=valuation_id, signed_at=utcnow() if signed else None,
    )
    db_session.add(contract)
    db_session.commit()
    return contract


def test_mark_used_by_hand_refuses_a_valuation_no_signed_contract_carries(db_session):
    """KAN-115 (Anto, 2026-10-07): only a signed deal stamps a valuation
    «Verwendet». A pending contract, or a signed one of another dealership,
    is no signed deal of this one."""

    tenant_id = uuid.uuid4()
    valuation = _manual_valuation(db_session, tenant_id)
    _contract_carrying(db_session, valuation.id, tenant_id=tenant_id, signed=False, status=ContractStatus.PENDING)
    _contract_carrying(db_session, valuation.id, tenant_id=uuid.uuid4(), signed=True)

    with pytest.raises(ConflictError) as exc:
        mark_used_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4())

    assert exc.value.details["reason"] == "no_signed_contract"
    db_session.expire_all()
    assert db_session.get(Valuation, valuation.id).used_at is None


def test_mark_used_by_hand_stamps_an_expired_valuation_a_signed_contract_carries(db_session):
    """The repair for a contract signed before KAN-101 stamped automatically:
    its valuation has usually expired since, and still has to be stamped."""

    tenant_id = uuid.uuid4()
    valuation = _manual_valuation(db_session, tenant_id, expired=True)
    _contract_carrying(db_session, valuation.id, tenant_id=tenant_id, signed=True)

    used = mark_used_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4())

    assert derive_status(used) == "used"


def test_mark_used_by_hand_counts_a_cancelled_contract_that_was_signed(db_session):
    """Cancelling a signed contract leaves its valuation used (ADR-066)."""

    tenant_id = uuid.uuid4()
    valuation = _manual_valuation(db_session, tenant_id)
    _contract_carrying(db_session, valuation.id, tenant_id=tenant_id, signed=True, status=ContractStatus.CANCELLED)

    assert derive_status(mark_used_by_hand(db_session, valuation=valuation, actor_id=uuid.uuid4())) == "used"


def test_mark_used_by_hand_leaves_an_already_stamped_valuation_as_it_is(db_session):
    tenant_id = uuid.uuid4()
    valuation = _manual_valuation(db_session, tenant_id)
    stamped = mark_used(db_session, valuation=valuation, actor_id=None)
    used_at, version = stamped.used_at, stamped.version

    again = mark_used_by_hand(db_session, valuation=stamped, actor_id=uuid.uuid4())

    assert (again.used_at, again.version) == (used_at, version)


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


def _sales_principal() -> tuple[str, uuid.UUID]:
    tenant_id = uuid.uuid4()
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        group_id=uuid.uuid5(uuid.NAMESPACE_OID, str(tenant_id)),
        roles=frozenset({AccessRole.SALES}),
    )
    return token, tenant_id


def test_valuation_read_says_whether_a_signed_contract_carries_it(client, db_session):
    """KAN-115 — the screen enables «Als verwendet markieren» only for a
    valuation a signed contract carries, so every read says whether one does."""

    token, tenant_id = _sales_principal()
    carried = client.post(
        "/v1/valuations", json={"finalOffer": "800.00", "source": "manual"}, headers=_bearer(token)
    ).json()
    alone = client.post(
        "/v1/valuations", json={"finalOffer": "900.00", "source": "manual"}, headers=_bearer(token)
    ).json()
    assert carried["hasSignedContract"] is False
    _contract_carrying(db_session, uuid.UUID(carried["id"]), tenant_id=tenant_id, signed=True)

    assert client.get(f"/v1/valuations/{carried['id']}", headers=_bearer(token)).json()["hasSignedContract"] is True
    listed = {row["id"]: row["hasSignedContract"] for row in client.get("/v1/valuations", headers=_bearer(token)).json()["items"]}
    assert listed == {carried["id"]: True, alone["id"]: False}


def test_mark_used_over_http_refuses_a_valuation_no_signed_contract_carries(client):
    token, _tenant_id = _sales_principal()
    created = client.post(
        "/v1/valuations", json={"finalOffer": "700.00", "source": "manual"}, headers=_bearer(token)
    ).json()

    response = client.post(
        f"/v1/valuations/{created['id']}/mark-used",
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "no_signed_contract"


def test_mark_used_over_http_stamps_an_expired_valuation_a_signed_contract_carries(client, db_session):
    token, tenant_id = _sales_principal()
    created = client.post(
        "/v1/valuations", json={"finalOffer": "600.00", "source": "manual"}, headers=_bearer(token)
    ).json()
    valuation = db_session.get(Valuation, uuid.UUID(created["id"]))
    valuation.valid_until = utcnow() - dt.timedelta(days=1)
    db_session.commit()
    _contract_carrying(db_session, valuation.id, tenant_id=tenant_id, signed=True)

    response = client.post(
        f"/v1/valuations/{created['id']}/mark-used",
        headers={**_bearer(token), "If-Match": str(created["version"])},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "used"

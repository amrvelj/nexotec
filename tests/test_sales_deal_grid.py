"""WP-8 PR-1: sales_deal, the ADR-060 grid-facing read model.

Load-bearing case: the confirmed reference prototype's own grid shows
exactly ONE row per deal lineage — an offer that becomes a contract never
leaves a second, stale row behind (34 total = 19 offers + 15 contracts,
no double counting).
"""

import uuid
from decimal import Decimal

from app.core.pagination import SortPageParams
from app.core.sorting import SortField
from app.sales.models.deal import SalesDeal
from app.sales.services.contract import create_contract
from app.sales.services.deal import list_deals
from app.sales.services.deal_projection import upsert_deal_projection
from app.sales.services.offer import create_offer


def test_creating_an_offer_projects_a_deal_row(db_session):
    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())

    deal = db_session.get(SalesDeal, offer.id)
    assert deal is not None
    assert deal.entity_type == "offer"
    assert deal.number == offer.offer_number
    assert deal.status == "draft"


def test_creating_a_contract_from_an_offer_updates_the_same_row_in_place(db_session):
    """The offer's own id stays the deal's stable identity — this must be
    exactly ONE row, not two, before and after "Vertrag erzeugen".
    """

    tenant_id = uuid.uuid4()
    offer = create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    offer_deal_id = offer.id

    contract = create_contract(db_session, tenant_id=tenant_id, offer=offer, actor_id=uuid.uuid4())

    deal = db_session.get(SalesDeal, offer_deal_id)
    assert deal is not None
    assert deal.entity_type == "contract"
    assert deal.number == contract.contract_number
    assert deal.contract_id == contract.id
    assert deal.offer_id == offer.id
    assert deal.offer_number == offer.offer_number

    total_rows = db_session.query(SalesDeal).filter_by(tenant_id=tenant_id).count()
    assert total_rows == 1


def test_a_direct_contract_with_no_offer_gets_its_own_deal_row(db_session):
    tenant_id = uuid.uuid4()
    contract = create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    deal = db_session.get(SalesDeal, contract.id)
    assert deal is not None
    assert deal.entity_type == "contract"
    assert deal.offer_id is None


def test_upsert_deal_projection_requires_an_offer_or_a_contract(db_session):
    import pytest

    with pytest.raises(ValueError):
        upsert_deal_projection(db_session)


def test_list_deals_filters_by_entity_type(db_session):
    tenant_id = uuid.uuid4()
    create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    create_contract(db_session, tenant_id=tenant_id, offer=None, actor_id=uuid.uuid4())

    sort_fields = [SortField(api_name="updatedAt", column=SalesDeal.updated_at, direction="desc", nullable=False)]
    params = SortPageParams(limit=50, cursor=None, sort_fields=sort_fields)

    _all_rows, _cursor, total_all, _est = list_deals(db_session, tenant_id=tenant_id, q=None, entity_type=None, params=params)
    offers_only, _cursor, total_offers, _est = list_deals(
        db_session, tenant_id=tenant_id, q=None, entity_type="offer", params=params
    )

    assert total_all == 3
    assert total_offers == 2
    assert all(row.entity_type == "offer" for row in offers_only)


def test_margin_column_exists_but_is_never_populated_by_pr1(db_session):
    """PR-3 is the first PR that ever writes this column — pinned here so
    a later PR notices it went from always-null to something real, rather
    than silently."""

    tenant_id = uuid.uuid4()
    create_offer(db_session, tenant_id=tenant_id, actor_id=uuid.uuid4())
    deal = db_session.query(SalesDeal).filter_by(tenant_id=tenant_id).one()
    assert deal.margin is None
    assert isinstance(deal.margin, Decimal) or deal.margin is None

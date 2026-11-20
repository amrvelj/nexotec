"""KAN-769 (ADR-190): the group stock listing's Betrieb filter lists the Betriebe by label under
the collation — case ignored, Ä/é beside A/e — not by `casefold()` («ärztin» after «z»), equal
labels by id. The expected order is the database's own (`tests.text_sort_support`)."""

from app.inventory.services.group_listing import list_group_stock_dealerships
from app.platform.organisation.models.dealership import DealerGroup
from tests.inventory.test_inventory_group_listing import _make_dealership
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order

NAMES = ["Zeta", "alpha", "Ärztin", "Arzt", "école"]


@POSTGRES_SQL
def test_kan769_group_stock_filter_betriebe_sort_by_label_under_the_collation_ties_by_id(db_session):
    group = DealerGroup(name="Gruppe Muster", group_read_enabled=True)
    db_session.add(group)
    db_session.commit()
    twins = []
    for index, name in enumerate([*NAMES, "Arzt"]):
        dealership = _make_dealership(db_session, group, license_number=str(index))
        dealership.legal_name = name
        if name == "Arzt":
            twins.append(dealership.id)
    db_session.commit()

    refs = list_group_stock_dealerships(
        db_session, principal_group_id=group.id, requested_group_id=group.id, is_authorized=lambda: True
    )

    labels = [ref.label for ref in refs]
    expected = text_sort_order(db_session, [*NAMES, "Arzt"])
    assert expected != sorted(expected), "byte order and collated order must differ"
    assert expected != sorted(expected, key=str.casefold), "the old casefold key must disagree too"
    assert labels == expected
    assert [ref.id for ref in refs if ref.label == "Arzt"] == sorted(twins, key=str)

"""KAN-769 (ADR-190): the duplicate check lists exact matches first, each group by name (last
name, else company name) under the collation — case ignored, Ä/é beside A/e — not by `.lower()`
(«ärztin» after «z»). The expected order is the database's own (`tests.text_sort_support`)."""

import uuid

from app.customer.models.customer import Customer, CustomerEmail, CustomerType, EmailType, Language
from app.customer.services.customer_search import duplicate_check
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order

SIMILAR = ["Zeta", "alpha", "Ärztin", "Arzt"]
SIMILAR_COMPANY = "école SA"
EXACT = ["zürcher", "Bach", "Égli"]
NEEDLE = "ada@example.ch"


def _customer(db, group_id: uuid.UUID, email: str, *, last_name: str | None = None, company: str | None = None) -> None:
    customer = Customer(
        group_id=group_id, customer_number=f"K-{uuid.uuid4().hex[:6]}",
        customer_type=CustomerType.BUSINESS if company else CustomerType.INDIVIDUAL, language=Language.DE,
        first_name=None if company else "Ada", last_name=last_name, company_name=company,
    )
    db.add(customer)
    db.flush()
    db.add(CustomerEmail(group_id=group_id, customer_id=customer.id, email_type=EmailType.PERSONAL, email_address=email))


@POSTGRES_SQL
def test_kan769_duplicate_check_lists_exact_then_similar_each_by_name_under_the_collation(db_session):
    group_id = uuid.uuid4()
    for index, name in enumerate(SIMILAR):
        _customer(db_session, group_id, f"{index}.{NEEDLE}", last_name=name)
    _customer(db_session, group_id, f"9.{NEEDLE}", company=SIMILAR_COMPANY)
    for name in EXACT:
        _customer(db_session, group_id, NEEDLE, last_name=name)
    db_session.commit()

    results = duplicate_check(db_session, group_id=group_id, q=NEEDLE)

    names = [(r["match"], r["last_name"] or r["company_name"]) for r in results]
    exact = text_sort_order(db_session, EXACT)
    similar = text_sort_order(db_session, [*SIMILAR, SIMILAR_COMPANY])
    assert similar != sorted(similar) and similar != sorted(similar, key=str.lower), "the orders must differ"
    assert exact != sorted(exact, key=str.lower), "the orders must differ"
    assert names == [("exact", n) for n in exact] + [("similar", n) for n in similar]

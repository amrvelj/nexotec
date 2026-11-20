"""KAN-769: `app.core.text_collation.sort_by_text` orders a list that is not a query's rows (a
provider's live answer) by asking the database for the collated order — one statement, none for
an empty list, equal keys in their given order."""

from dataclasses import dataclass
from typing import Self

from sqlalchemy import event

from app.core.text_collation import sort_by_text
from tests.postgres_only import POSTGRES_SQL
from tests.text_sort_support import text_sort_order


@dataclass(frozen=True)
class _Item:
    name: str
    tag: int


class _Statements:
    """Counts the statements the session's engine runs while open."""

    def __init__(self, db_session) -> None:
        self.engine = db_session.get_bind()
        self.count = 0

    def _count(self, *_args, **_kwargs) -> None:
        self.count += 1

    def __enter__(self) -> Self:
        event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._count)


def test_kan769_an_empty_list_needs_no_statement(db_session):
    with _Statements(db_session) as statements:
        assert sort_by_text(db_session, [], key=lambda item: item.name) == []
        assert sort_by_text(db_session, iter(()), key=lambda item: item.name) == []
    assert statements.count == 0


@POSTGRES_SQL
def test_kan769_an_umlaut_sorts_beside_its_base_letter_in_one_statement(db_session):
    names = ["Zeta", "alpha", "Ärztin", "Arzt", "école", "Ezra", "Šimon", "Sara"]
    items = [_Item(name, index) for index, name in enumerate(names)]

    with _Statements(db_session) as statements:
        ordered = sort_by_text(db_session, items, key=lambda item: item.name)

    assert statements.count == 1
    assert [item.name for item in ordered] == text_sort_order(db_session, names)
    assert [item.name for item in ordered] == ["alpha", "Arzt", "Ärztin", "école", "Ezra", "Sara", "Šimon", "Zeta"]
    assert [item.name for item in ordered] != sorted(names)
    assert [item.name for item in ordered] != sorted(names, key=str.lower)


@POSTGRES_SQL
def test_kan769_equal_keys_keep_their_given_order(db_session):
    items = [_Item("Bern", 3), _Item("aarau", 0), _Item("Bern", 1), _Item("Bern", 2), _Item("Ärztin", 9)]

    ordered = sort_by_text(db_session, items, key=lambda item: item.name)

    assert [(item.name, item.tag) for item in ordered] == [
        ("aarau", 0), ("Ärztin", 9), ("Bern", 3), ("Bern", 1), ("Bern", 2),
    ]
    assert sort_by_text(db_session, list(reversed(items)), key=lambda item: item.name)[2:] == [
        _Item("Bern", 2), _Item("Bern", 1), _Item("Bern", 3),
    ]

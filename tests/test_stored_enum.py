"""StoredEnum (app/core/enum_type.py), KAN-86 step 1: decoding, encoding and
the widened comparisons, on a throwaway table so no model's vocabulary is
involved.

The table is DDL, and any DDL in a test has the shared schema rebuilt
before the next one (tests/conftest.py), so the database checks share two
tests rather than one each.
"""

import enum

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import StatementError

from app.core.enum_type import StoredEnum


class Colour(str, enum.Enum):
    DARK_RED = "dark_red"
    BLUE = "blue"


_metadata = MetaData()
_paint = Table(
    "kan86_paint", _metadata, Column("id", Integer, primary_key=True), Column("colour", StoredEnum(Colour, length=16))
)


@pytest.fixture()
def paint(engine):
    _metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO kan86_paint (id, colour) VALUES (1, 'DARK_RED'), (2, 'dark_red'), (3, 'BLUE'), (4, NULL)")
            )
        yield engine
    finally:
        _metadata.drop_all(engine)


def _ids(engine, where) -> list[int]:
    with engine.connect() as conn:
        return sorted(conn.scalars(select(_paint.c.id).where(where)))


def test_reading_and_writing(paint):
    # Both stored forms decode to the member.
    with paint.connect() as conn:
        rows = dict(conn.execute(select(_paint.c.id, _paint.c.colour)).all())
    assert rows == {1: Colour.DARK_RED, 2: Colour.DARK_RED, 3: Colour.BLUE, 4: None}

    # A write stores the member NAME, from a member or from either string form.
    with paint.begin() as conn:
        conn.execute(insert(_paint), [{"id": 6, "colour": Colour.BLUE}, {"id": 7, "colour": "dark_red"}])
    with paint.connect() as conn:
        stored = dict(conn.execute(text("SELECT id, colour FROM kan86_paint WHERE id IN (6, 7)")).all())
    assert stored == {6: "BLUE", 7: "DARK_RED"}

    # Writing an unknown string is refused.
    with paint.begin() as conn, pytest.raises(StatementError, match="'GREEN' is not among"):
        conn.execute(insert(_paint), [{"id": 8, "colour": "GREEN"}])

    # A stored string that is neither form still raises on read (KAN-60's guard relies on it).
    with paint.begin() as conn:
        conn.execute(text("INSERT INTO kan86_paint (id, colour) VALUES (5, 'GREEN')"))
    with paint.connect() as conn, pytest.raises(LookupError, match="'GREEN' is not among the defined enum values"):
        conn.execute(select(_paint.c.colour).where(_paint.c.id == 5)).all()


def test_filters_match_both_forms_and_never_raise(paint):
    colour = _paint.c.colour
    # == and != match both forms; NULL rows stay out of !=, as with a plain column.
    assert _ids(paint, colour == Colour.DARK_RED) == [1, 2]
    assert _ids(paint, colour == "dark_red") == [1, 2]
    assert _ids(paint, colour == "DARK_RED") == [1, 2]
    assert _ids(paint, colour != Colour.DARK_RED) == [3]
    # in_ / not_in widen the same way.
    assert _ids(paint, colour.in_([Colour.DARK_RED, Colour.BLUE])) == [1, 2, 3]
    assert _ids(paint, colour.in_(["blue"])) == [3]
    assert _ids(paint, colour.not_in([Colour.BLUE])) == [1, 2]
    assert _ids(paint, colour.in_([])) == []
    # IS NULL is untouched.
    assert _ids(paint, colour.is_(None)) == [4]
    assert _ids(paint, colour == None) == [4]  # the ORM spelling of IS NULL
    # An unknown filter string matches nothing (and != every non-NULL row),
    # as sqlalchemy.Enum did — filters can carry client input, so no raise.
    assert _ids(paint, colour == "green") == []
    assert _ids(paint, colour != "green") == [1, 2, 3]
    assert _ids(paint, colour.in_(["green", "blue"])) == [3]
    assert _ids(paint, colour.in_(["green"])) == []
    assert _ids(paint, colour.not_in(["green"])) == [1, 2, 3]


def test_an_ambiguous_vocabulary_is_refused():
    class Clash(str, enum.Enum):
        A = "B"
        B = "c"

    with pytest.raises(TypeError, match="'B' names two different members"):
        StoredEnum(Clash, length=8)

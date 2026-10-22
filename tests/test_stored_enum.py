"""StoredEnum (app/core/enum_type.py) since KAN-175 (KAN-86 step 3): a column
holds `.value` and nothing else — decoding, encoding, filtering, and the
CHECK constraint each column carries — on a throwaway table, so no model's
vocabulary is involved.

The table is DDL, and any DDL in a test has the shared schema rebuilt before
the next one (tests/conftest.py), so the database checks share two tests
rather than one each.
"""

import enum

import pytest
from alembic.autogenerate import render_python_code
from alembic.operations import ops
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateTable

from app.core.enum_type import StoredEnum, enum_check_condition, enum_check_name, render_autogenerate_item


class Colour(str, enum.Enum):
    DARK_RED = "dark_red"
    BLUE = "blue"


class Shade(str, enum.Enum):
    """Same values as Colour, different class."""

    DARK_RED = "dark_red"
    BLUE = "blue"


class Tint(str, enum.Enum):
    """A member NAME Colour has, with a value Colour does not."""

    BLUE = "azure"


_metadata = MetaData()
_paint = Table(
    "kan86_paint", _metadata, Column("id", Integer, primary_key=True), Column("colour", StoredEnum(Colour, length=16))
)


@pytest.fixture()
def paint(engine):
    _metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO kan86_paint (id, colour) VALUES (1, 'dark_red'), (2, 'blue'), (3, NULL)"))
        yield engine
    finally:
        _metadata.drop_all(engine)


def _ids(engine, where) -> list[int]:
    with engine.connect() as conn:
        return sorted(conn.scalars(select(_paint.c.id).where(where)))


def test_reading_writing_and_the_check_constraint(paint):
    with paint.connect() as conn:
        rows = dict(conn.execute(select(_paint.c.id, _paint.c.colour)).all())
    assert rows == {1: Colour.DARK_RED, 2: Colour.BLUE, 3: None}

    # A write stores the .value, from a member, its value, or another
    # class's member with the same value.
    with paint.begin() as conn:
        conn.execute(insert(_paint), [{"id": 6, "colour": Colour.BLUE}, {"id": 7, "colour": "dark_red"}])
        conn.execute(insert(_paint), [{"id": 9, "colour": Shade.BLUE}])
    with paint.connect() as conn:
        stored = dict(conn.execute(text("SELECT id, colour FROM kan86_paint WHERE id IN (6, 7, 9)")).all())
    assert stored == {6: "blue", 7: "dark_red", 9: "blue"}

    # The member NAME is no longer a way to write it, nor is an unknown string.
    for refused in ("DARK_RED", "GREEN"):
        with paint.begin() as conn, pytest.raises(StatementError, match=f"'{refused}' is not among"):
            conn.execute(insert(_paint), [{"id": 8, "colour": refused}])

    # Past the ORM, the column's CHECK refuses both too (KAN-175).
    for refused in ("DARK_RED", "GREEN"):
        with paint.begin() as conn, pytest.raises(IntegrityError, match="ck_kan86_paint_colour_enum"):
            conn.execute(text(f"INSERT INTO kan86_paint (id, colour) VALUES (10, '{refused}')"))


def test_filters_compare_the_value_and_never_raise(paint):
    colour = _paint.c.colour
    assert _ids(paint, colour == Colour.DARK_RED) == [1]
    assert _ids(paint, colour == "dark_red") == [1]
    assert _ids(paint, colour != Colour.DARK_RED) == [2]  # NULL stays out of !=, as with a plain column
    assert _ids(paint, colour.in_([Colour.DARK_RED, Colour.BLUE])) == [1, 2]
    assert _ids(paint, colour.not_in([Colour.BLUE])) == [1]
    assert _ids(paint, colour.in_([])) == []
    assert _ids(paint, colour.is_(None)) == [3]
    assert _ids(paint, colour == None) == [3]  # the ORM spelling of IS NULL
    # A string that is no value — a name, or client input — matches nothing
    # and never raises (a filter can carry `?role=`).
    assert _ids(paint, colour == "DARK_RED") == []
    assert _ids(paint, colour == "green") == []
    assert _ids(paint, colour != "green") == [1, 2]
    assert _ids(paint, colour.in_(["green", "blue"])) == [2]
    assert _ids(paint, colour.not_in(["green"])) == [1, 2]
    # A member of another class with the same values resolves by its value.
    assert _ids(paint, colour == Shade.BLUE) == [2]
    # Plain equality: the poller's partial index (status = 'pending') stays usable.
    compiled = str((colour == Colour.BLUE).compile(compile_kwargs={"literal_binds": True}))
    assert compiled == "kan86_paint.colour = 'blue'"


def test_a_stored_name_no_longer_reads():
    """Step 3: reads are strict. (The CHECK keeps a name out; this is the
    type itself, as a row from before KAN-175's sweep would meet it.)"""

    column_type = StoredEnum(Colour, length=16)

    assert column_type.process_result_value("dark_red", None) is Colour.DARK_RED
    with pytest.raises(LookupError, match="'DARK_RED' is not among the defined enum values"):
        column_type.process_result_value("DARK_RED", None)


def test_another_classs_member_resolves_by_its_value_never_by_its_name():
    """As sqlalchemy.Enum did. Tint.BLUE is 'azure': matching it to
    Colour.BLUE by name would write a colour nobody chose."""

    column_type = StoredEnum(Colour, length=16)

    assert column_type.process_bind_param(Shade.BLUE, None) == "blue"
    with pytest.raises(LookupError, match="is not among the defined enum values"):
        column_type.process_bind_param(Tint.BLUE, None)


def _check_names(table: Table) -> list[str]:
    return sorted(c.name for c in table.constraints if isinstance(c, CheckConstraint))


def test_a_copied_table_carries_each_check_once():
    """KAN-297: Table.to_metadata() copies the table's constraints and attaches
    the copied column, which attaches its own CHECK. The copy carries one, named
    after the table it now belongs to."""

    assert _check_names(_paint.to_metadata(MetaData())) == ["ck_kan86_paint_colour_enum"]

    renamed = _paint.to_metadata(MetaData(), name="kan297_paint")
    assert _check_names(renamed) == ["ck_kan297_paint_colour_enum"]
    ddl = str(CreateTable(renamed).compile(dialect=postgresql.dialect()))
    assert "CONSTRAINT ck_kan297_paint_colour_enum CHECK (colour IN ('dark_red', 'blue'))" in ddl


def test_autogenerate_renders_each_check_once():
    """Alembic's op.to_table(), which renders autogenerate's create_table,
    copies the columns — each attaching its own CHECK — and then appends the
    copied constraints. The second copy of a name is dropped, so a generated
    migration does not create the same constraint twice (Postgres refuses it).
    And it creates it at all: since alembic 1.20.1 autogenerate skips a
    type-bound CHECK, so env.py's hook (render_autogenerate_item) renders it
    (KAN-417)."""

    create = ops.CreateTableOp.from_table(_paint)
    assert _check_names(create.to_table()) == ["ck_kan86_paint_colour_enum"]

    rendered = render_python_code(ops.UpgradeOps(ops=[create]), render_item=render_autogenerate_item)
    assert rendered.count("sa.CheckConstraint(") == 1
    assert "sa.CheckConstraint(\"colour IN ('dark_red', 'blue')\", name='ck_kan86_paint_colour_enum')" in rendered


def test_the_check_constraint_is_named_and_shaped_for_its_migration():
    assert enum_check_name("stock_item", "condition") == "ck_stock_item_condition_enum"
    assert enum_check_condition("condition", ["new", "used"]) == "\"condition\" IN ('new', 'used')"
    with pytest.raises(ValueError, match="longer than 63"):
        enum_check_name("t" * 40, "c" * 20)

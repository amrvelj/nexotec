"""The order a word column sorts in (KAN-740, ADR-190, `app.core.text_collation`), for tests that
compare an API's order with an expected one: asked of the database itself — the same `ORDER BY …
COLLATE "nexotec_text_sort"` the lists run — never mirrored in Python (a Python key drifts from
ICU on punctuation, `_` and `&` among them), and never Python's plain `sorted()`, which is byte
order. On SQLite (the fast lane, never verification) a sort carries no collation, so the order
there is byte order: `sorted()`."""

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from sqlalchemy.types import Text

from app.core.text_collation import TEXT_SORT_COLLATION


def text_sort_order(bind: Engine | Connection | Session, values: Sequence[str], *, descending: bool = False) -> list[str]:
    """`values` in the order the collated sort puts them, `descending` reversed."""

    dialect = bind.get_bind().dialect if isinstance(bind, Session) else bind.dialect
    if dialect.name != "postgresql":
        return sorted(values, reverse=descending)
    direction = "DESC" if descending else "ASC"
    statement = text(
        f'SELECT v FROM unnest(:values) WITH ORDINALITY AS t(v, n) ORDER BY v COLLATE "{TEXT_SORT_COLLATION}" {direction}'
    ).bindparams(bindparam("values", type_=ARRAY(Text())))
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            return list(connection.scalars(statement, {"values": list(values)}))
    return list(bind.scalars(statement, {"values": list(values)}))

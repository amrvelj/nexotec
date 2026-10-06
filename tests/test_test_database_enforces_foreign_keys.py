"""Both test lanes reject a row whose parent does not exist (KAN-87).

SQLite ignores foreign keys unless ``PRAGMA foreign_keys=ON`` is set on each
connection; ``tests/conftest.py::_make_engine`` sets it. This test fails on
the SQLite fast lane if that listener is ever removed, and pins the same
behaviour on the Postgres lane of record.
"""

import uuid

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from app.core.base import utcnow
from app.platform.models.reference_data import ReferenceValue


def test_a_child_row_without_its_parent_is_rejected(engine):
    now = utcnow()
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(ReferenceValue.__table__).values(
                id=uuid.uuid4(),
                list_id=uuid.uuid4(),  # no reference_list row has this id
                value_code="XX",
                label_de="x",
                label_fr="x",
                label_it="x",
                label_en="x",
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
        )

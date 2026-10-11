"""StoredEnum: the one column type for every Python-enum column (KAN-86).

Why it exists. `sqlalchemy.Enum(..., native_enum=False)` without a
`values_callable` stores the enum member NAME (`EMAIL`), never `.value`
(`email`). Migrations kept being written against `.value` — KAN-60 (a
rename that matched zero rows, then a 500 on `GET /v1/customers`) and
KAN-91 (a migration that lowercased `consent_source` into values the ORM
could not decode). KAN-86 moved every enum column to storing `.value`, in
three steps, because a deploy runs old and new code side by side for a
while (Render migrates on startup while the old instances still serve):

1. Every column read EITHER form, and every equality filter matched EITHER
   form. Writes still stored the NAME.
2. Writes stored `.value`, and one migration per context rewrote existing
   names to values. Step-1 code still running during that deploy read,
   filtered and wrote correctly against the rewritten rows.
3. (KAN-175, this module as it stands) A second idempotent sweep per context
   rewrote the names step-1 instances wrote during step 2's overlap, and
   reads and filters became strict: a column holds `.value` and nothing
   else. Step-2 code still running during this deploy writes values and
   filters on both forms, so the overlap is safe.

Since step 3 every StoredEnum column also carries a CHECK constraint
`"{column}" IN ({values})` (`ck_{table}_{column}_enum`), attached here when
the column joins its table and created on existing tables by the KAN-175
migrations: an unknown string can no longer be stored, so every list
endpoint is safe against undecodable rows. Alembic's autogenerate does not
compare CHECK constraints, so a new enum column, or a new member, needs its
constraint created (or replaced) in its migration by hand;
tests/test_migrated_enum_checks.py fails until it is (KAN-296).

What it does NOT do: invent a member for a stored string that is not a
value. That still raises `LookupError`, exactly as `sqlalchemy.Enum` did.
A filter is different: it may take a client-supplied string (`?role=` on the
user list), so an equality or IN filter on a string that is no value matches
nothing instead of raising (a 500).
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from typing import Any

from sqlalchemy import CheckConstraint, Column, String, Table, event, literal, type_coerce
from sqlalchemy.sql import operators
from sqlalchemy.types import TypeDecorator

# Postgres truncates identifiers longer than this; a truncated constraint
# name would no longer match the one its migration created.
_MAX_IDENTIFIER_LENGTH = 63


def enum_check_name(table: str, column: str) -> str:
    """The name of the CHECK constraint on an enum column (KAN-175)."""

    name = f"ck_{table}_{column}_enum"
    if len(name) > _MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name}: longer than {_MAX_IDENTIFIER_LENGTH} characters — choose a shorter name")
    return name


def enum_check_condition(column: str, values: list[str]) -> str:
    """The CHECK constraint's condition: the column holds one of `values`."""

    return f'"{column}" IN ({", ".join(_quote(value) for value in values)})'


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class StoredEnum(TypeDecorator):
    """A VARCHAR column holding the `.value` of a member of `enum_class`.

    Declared as `StoredEnum(MyEnum, length=16)`, in place of
    `SAEnum(MyEnum, native_enum=False, length=16)`. `length` is required:
    every existing column has an explicit one and a migration owns it.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[enum.Enum], *, length: int) -> None:
        super().__init__(length=length)
        self.enum_class = enum_class
        self.length = length
        lookup: dict[str, enum.Enum] = {}
        for member in enum_class:
            if not isinstance(member.value, str):
                raise TypeError(f"{enum_class.__name__}.{member.name}: StoredEnum needs string values")
            lookup[member.value] = member
        self._lookup = lookup

    @property
    def values(self) -> list[str]:
        """Every value the column may hold, in definition order."""

        return list(self._lookup)

    # --- decoding ---------------------------------------------------------

    def _resolve(self, item: Any) -> enum.Enum | None:
        """The member `item` denotes, or None. A member of another enum
        class (Language vs SwissLanguage share their values) resolves by
        its value, as sqlalchemy.Enum did."""

        if isinstance(item, self.enum_class):
            return item
        if isinstance(item, enum.Enum):
            return self._lookup.get(str(item.value))
        if isinstance(item, str):
            return self._lookup.get(item)
        return None

    def member_for(self, stored: str) -> enum.Enum:
        """The member a stored (or submitted) value denotes."""

        try:
            return self._lookup[stored]
        except KeyError:
            raise LookupError(
                f"'{stored}' is not among the defined enum values. Enum name: "
                f"{self.enum_class.__name__.lower()}. Possible values: {', '.join(self._lookup)}"
            ) from None

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        member = self._resolve(value) or self.member_for(value)
        return str(member.value)

    def process_literal_param(self, value: Any, dialect: Any) -> str:
        return _quote(str(self.process_bind_param(value, dialect)))

    def process_result_value(self, value: Any, dialect: Any) -> enum.Enum | None:
        if value is None:
            return None
        return self.member_for(value)

    def copy(self, **kw: Any) -> StoredEnum:
        return StoredEnum(self.enum_class, length=self.length)

    # --- filtering --------------------------------------------------------

    class comparator_factory(TypeDecorator.Comparator):
        """`==`, `!=`, `in_` and `not_in` against members or strings compare
        the stored value. A string that is no value is compared as it is, so
        it matches nothing and never raises — a filter may take a
        client-supplied string. NULL handling is SQL's own."""

        def _stored(self, other: Any) -> list[Any] | None:
            column_type: StoredEnum = self.type  # type: ignore[assignment]
            if isinstance(other, (str, enum.Enum)):
                items = [other]
            elif isinstance(other, Iterable) and not isinstance(other, bytes):
                items = list(other)
                if not items or not all(isinstance(i, (str, enum.Enum)) for i in items):
                    return None  # empty, or expressions: SQLAlchemy's own handling
            else:
                return None
            stored: list[Any] = []
            for item in items:
                member = column_type._resolve(item)
                text_form = str(member.value) if member is not None else (
                    str(item.value) if isinstance(item, enum.Enum) else str(item)
                )
                stored.append(literal(text_form, String()))
            return stored

        def operate(self, op: Any, *other: Any, **kwargs: Any) -> Any:
            if len(other) == 1 and op in (operators.eq, operators.ne, operators.in_op, operators.not_in_op):
                stored = self._stored(other[0])
                if stored is not None:
                    if op is operators.eq:
                        return self.expr == stored[0] if len(stored) == 1 else self.expr.in_(stored)
                    if op is operators.ne:
                        return self.expr != stored[0] if len(stored) == 1 else self.expr.not_in(stored)
                    return self.expr.in_(stored) if op is operators.in_op else self.expr.not_in(stored)
            return super().operate(op, *other, **kwargs)

    # --- the CHECK constraint ---------------------------------------------

    def _set_parent(self, parent: Any, outer: bool = False, **kw: Any) -> None:
        super()._set_parent(parent, outer=outer, **kw)
        if isinstance(parent, Column):
            event.listen(parent, "after_parent_attach", self._attach_check)

    def _attach_check(self, column: Column[Any], table: Any) -> None:
        if not isinstance(table, Table):
            return
        name = enum_check_name(table.name, column.name)
        if any(getattr(constraint, "name", None) == name for constraint in table.constraints):
            return
        # Built as sqlalchemy.Enum builds its own CHECK: on the column, and
        # type-bound. Table.to_metadata() then skips it and the copied column
        # attaches its own (KAN-297) — one CHECK per copy, named after the
        # copy's table. Postgres stores it exactly as it stores a migration's
        # enum_check_condition() (tests/test_migrated_enum_checks.py), and
        # Alembic's autogenerate still renders it in a create_table.
        table.append_constraint(
            CheckConstraint(
                type_coerce(column, String()).in_(self.values), name=name, _autoattach=False, _type_bound=True
            )
        )

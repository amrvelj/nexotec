"""StoredEnum: the one column type for every Python-enum column (KAN-86).

Why it exists. `sqlalchemy.Enum(..., native_enum=False)` without a
`values_callable` stores the enum member NAME (`EMAIL`), never `.value`
(`email`). Migrations kept being written against `.value` — KAN-60 (a
rename that matched zero rows, then a 500 on `GET /v1/customers`) and
KAN-91 (a migration that lowercased `consent_source` into values the ORM
could not decode). KAN-86 moves every enum column to storing `.value`, in
three steps, because a deploy runs old and new code side by side for a
while (Render migrates on startup while the old instances still serve, and
the worker rewrites `outbox_message.status` every few seconds):

1. Every column reads EITHER form, and every equality filter matches EITHER
   form. Writes still store the NAME. Nothing in the database changes yet.
2. Writes store `.value`, and one migration per context rewrites existing
   names to values. Code from step 1 still running during that deploy
   reads, filters and writes correctly against the rewritten rows.
3. A second idempotent sweep catches names written during step 2's
   overlap; reads and filters become strict (`.value` only). That step is KAN-175.

`_WRITE_FORM` is the step switch. Until step 3 lands, nothing in the
database is guaranteed to be in one form — never write raw SQL against an
enum column that assumes one; compare through the ORM column instead,
which matches both.

What it does NOT do: invent a member for a stored string that is neither a
member name nor a value. That still raises `LookupError`, exactly as
`sqlalchemy.Enum` did, so KAN-60's isolate-and-skip in `list_customers`
keeps working and a genuinely unknown value stays loud.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import String, false, literal
from sqlalchemy.sql import operators
from sqlalchemy.types import TypeDecorator

# Which form a write stores: "name" until KAN-86 step 2, then "value".
_WRITE_FORM = "name"


class StoredEnum(TypeDecorator):
    """A VARCHAR column holding a member of `enum_class`.

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
            for form in (member.name, member.value):
                if lookup.get(form, member) is not member:
                    # One member's value spelled like another member's name
                    # would make a stored string ambiguous.
                    raise TypeError(f"{enum_class.__name__}: '{form}' names two different members")
                lookup[form] = member
        self._lookup = lookup

    # --- decoding ---------------------------------------------------------

    def member_for(self, stored: str) -> enum.Enum:
        """The member a stored (or submitted) string denotes, in either form."""

        try:
            return self._lookup[stored]
        except KeyError:
            raise LookupError(
                f"'{stored}' is not among the defined enum values. Enum name: "
                f"{self.enum_class.__name__.lower()}. Possible values: "
                f"{', '.join(m.name for m in self.enum_class)}"
            ) from None

    def stored_forms(self, member: enum.Enum) -> list[str]:
        """Every string a row holding `member` may contain right now."""

        return [member.name] if member.name == member.value else [member.name, member.value]

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        member = value if isinstance(value, self.enum_class) else self.member_for(value)
        return member.name if _WRITE_FORM == "name" else member.value

    def process_result_value(self, value: Any, dialect: Any) -> enum.Enum | None:
        if value is None:
            return None
        return self.member_for(value)

    def copy(self, **kw: Any) -> StoredEnum:
        return StoredEnum(self.enum_class, length=self.length)

    # --- filtering --------------------------------------------------------

    class comparator_factory(TypeDecorator.Comparator):
        """`col == X` becomes `col IN (<name>, <value>)`; `!=`, `in_` and
        `not_in` widen the same way. NULL handling is unchanged: `!=` and
        `NOT IN` both leave NULL rows out, as before.

        A filter string that is neither a name nor a value of the enum
        matches nothing (and `!=` matches every non-NULL row) — the same
        result `sqlalchemy.Enum` gave by passing it through to SQL. It must
        never raise: some filters take a client-supplied string (e.g.
        `?role=` on the user list), and that would be a 500. Writes still
        refuse an unknown string (`process_bind_param`).

        NOT widened, so they see one form only while a table holds both:
        ordering (`ORDER BY`, `<`, `>`, `BETWEEN`), `IS [NOT] DISTINCT FROM`,
        `case(value=col)`, column-to-column comparison, explicit
        `bindparam()`s, and unique constraints. KAN-86 step 2 has to keep
        each of these in mind for the deploy overlap.
        """

        def _forms(self, other: Any) -> list[Any] | None:
            column_type = cast(StoredEnum, self.type)
            if isinstance(other, (column_type.enum_class, str)):
                items = [other]
            elif isinstance(other, Iterable) and not isinstance(other, bytes):
                items = list(other)
                if not all(isinstance(i, (column_type.enum_class, str)) for i in items):
                    return None
            else:
                return None
            # Plain String literals, so the bind side passes each form
            # through unchanged instead of re-encoding it to _WRITE_FORM.
            forms: list[str] = []
            for item in items:
                member = item if isinstance(item, column_type.enum_class) else column_type._lookup.get(str(item))
                if member is None:
                    continue  # unknown filter value: matches nothing
                for form in column_type.stored_forms(member):
                    if form not in forms:
                        forms.append(form)
            return [literal(form, String()) for form in forms]

        def operate(self, op: Any, *other: Any, **kwargs: Any) -> Any:
            if len(other) == 1 and op in (operators.eq, operators.ne, operators.in_op, operators.not_in_op):
                if op in (operators.eq, operators.ne) and not isinstance(other[0], (str, enum.Enum)):
                    return super().operate(op, *other, **kwargs)
                forms = self._forms(other[0])
                if forms is not None:
                    positive = op in (operators.eq, operators.in_op)
                    if not forms:
                        return false() if positive else self.expr.is_not(None)
                    return self.expr.in_(forms) if positive else self.expr.not_in(forms)
            return super().operate(op, *other, **kwargs)

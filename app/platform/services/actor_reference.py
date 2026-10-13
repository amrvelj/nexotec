"""Who wrote a row, as nightly reconciliation checks it (KAN-243; Anto,
2026-10-12: created_by / updated_by are checked everywhere).

Every `created_by` / `updated_by` — TimestampMixin's, and the same-named
columns a few append-only tables declare themselves — holds the id of the
platform `User` who made the change: the access token's subject, which is
only ever minted for a `User` row (`app.platform.api.auth._mint_session`).
It is null when no user made it: a consumer, a scheduled job, a provider
sync, a migration or a legacy import (`scripts/migrate_transaction_rows.py`
writes null). Null names nobody, so it is never an orphan. There is no
sentinel or service-principal id.

Platform owns what a valid actor is, so each context with a nightly job
builds these checks here, through `app.platform.public`, instead of writing
the same ReferenceCheck by hand for every table.
"""

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase, aliased

from app.core.reconciliation import ReferenceCheck
from app.platform.models.user import User

ACTOR_COLUMNS = ("created_by", "updated_by")

# The target of every actor check is `user`, under an alias: platform's own
# job (KAN-318) checks user.created_by -> user.id, a self-join the core
# helper's plain outer join cannot compile (`"user"` named twice). An
# AliasedClass proxies __tablename__ and the mapped columns, which is all
# app.core.reconciliation reads from a check's target, and
# `sqlalchemy.inspect(_ACTOR).class_` is User, which is how
# tests/architecture/test_actor_columns_reconciled.py recognises it.
# Typed Any because ReferenceCheck.target_model is declared as a mapped
# class; the alias stands in for one here.
_ACTOR: Any = aliased(User, name="actor")


def actor_reference_checks(*models: type[DeclarativeBase]) -> list[ReferenceCheck]:
    """One nullable `{table}.{column} -> user.id` ReferenceCheck per actor
    column each model has. A finding is recorded against the row's first
    primary-key column (its id; for a composite key, its first part)."""

    checks: list[ReferenceCheck] = []
    for model in models:
        mapper: Any = inspect(model)
        columns = [name for name in ACTOR_COLUMNS if name in mapper.columns]
        if not columns:
            raise ValueError(f"{model.__name__} has no created_by / updated_by column to check.")
        row_id = getattr(model, mapper.get_property_by_column(mapper.primary_key[0]).key)
        for name in columns:
            checks.append(
                ReferenceCheck(
                    label=f"{model.__tablename__}.{name} -> user.id",
                    source_model=model,
                    source_row_id_column=row_id,
                    source_fk_column=getattr(model, name),
                    target_model=_ACTOR,
                    target_id_column=_ACTOR.id,
                    nullable=True,
                )
            )
    return checks

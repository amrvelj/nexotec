"""What `platform.public` hands another context about a user: frozen values,
never the mapped row (KAN-123 step 8, ADR-162). (`notify`, the other identity
read-and-write `public` re-exports, hands out the inbox row's id itself.)
With a `User` in hand another context could select,
mutate and flush platform's rows past the import-linter; with these it can
only read.

The functions below are `public`'s doors; inside platform the services keep
returning rows.
"""

import dataclasses
import uuid

from sqlalchemy.orm import Session

from app.platform.identity.models.user import User
from app.platform.identity.services import user_reads


@dataclasses.dataclass(frozen=True)
class ActingUser:
    """A user who may be responsible for an open record in a Betrieb (a
    seller, an advisor; KAN-410): who they are and how they are named and
    reached — what Customers, Sales and the identAPI enrolment read."""

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str

    @property
    def label(self) -> str:
        """`First Last`, the label another context copies beside the id (rule 2)."""

        return f"{self.first_name} {self.last_name}".strip()


def _acting_user(row: User) -> ActingUser:
    return ActingUser(id=row.id, first_name=row.first_name, last_name=row.last_name, email=row.email)


def get_active_user_acting_in(db: Session, *, dealership_id: uuid.UUID, user_id: uuid.UUID) -> ActingUser | None:
    """The user when they act in this Betrieb and are active or invited, else
    None (`user_reads.get_active_user_acting_in`, platform's one rule)."""

    row = user_reads.get_active_user_acting_in(db, dealership_id=dealership_id, user_id=user_id)
    return _acting_user(row) if row is not None else None


def list_active_users_acting_in(db: Session, *, dealership_id: uuid.UUID) -> list[ActingUser]:
    """Everyone `get_active_user_acting_in` admits for the Betrieb, ordered by name."""

    return [_acting_user(row) for row in user_reads.list_active_users_acting_in(db, dealership_id=dealership_id)]


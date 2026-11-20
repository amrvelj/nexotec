"""FR-A-17's exclusions (KAN-221 PR-4; Dealer Administration D-A-05, D-A-24):
what a Nexotec support session can never do, whatever the user it acts for
may do.

Under a support session the principal carries `acting_for` (`Principal`,
`app/core/auth.py`; the token that sets it is PR 3). A route that changes
credentials or MFA, the manager flag, an integration secret or a connected
mailbox, or that starts another impersonation, takes
`Depends(refuse_under_impersonation)`. It answers **403** with
`details.rule = "impersonation_excluded"` — the one deliberate 403 of this
API: the resource exists and the actor is known (the ticket's ruling; rule 7's
404 is for another tenant's record, not for an excluded actor).

The routes that carry it are named one by one in
`tests/platform/test_kan221_impersonation_exclusions.py`, which also fails on
a route that carries it without being named.

Cross-cutting by construction: it reads only the principal and imports no
context (CLAUDE.md rule 8).
"""

from fastapi import Depends

from app.core.auth import Principal, get_current_principal
from app.core.errors import ForbiddenError

IMPERSONATION_EXCLUDED = "impersonation_excluded"


def refuse_under_impersonation(principal: Principal = Depends(get_current_principal)) -> Principal:
    if principal.acting_for is not None:
        raise ForbiddenError(
            "A Nexotec support session cannot do this (FR-A-17).",
            details={"rule": IMPERSONATION_EXCLUDED},
        )
    return principal


def refuse_if_impersonating(principal: Principal) -> None:
    """The same refusal for a route that is excluded only when its body asks
    for an excluded change (the manager flag or the sign-in address on a user's
    PATCH, a new user or membership as manager, a connection's config)."""

    refuse_under_impersonation(principal)

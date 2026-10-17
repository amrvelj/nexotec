"""Every POST honours Idempotency-Key (API conventions, ADR-035; KAN-119).

A POST honours the key when it is an `IdempotentRoute` (app/core/
idempotent_route.py) — replay, 409 on reuse with a different request, a claim
against concurrent twins — and its OpenAPI declares the header, optional.
A header declared by hand is not enough: the hand-copied pattern stored the
key only after the work had committed, so two concurrent submits both did it.

The walk (`tests/route_walk.py`, shared since KAN-241) flattens included
routers: under FastAPI 0.141 `app.routes` holds only the top level. The first
test pins that the walk sees exactly the POSTs the OpenAPI publishes.
"""

from fastapi.routing import APIRoute

from app.core.idempotent_route import IDEMPOTENCY_KEY_HEADER, IdempotentRoute
from app.main import app
from tests.route_walk import api_routes

# Ratified by Anto on 2026-10-07 (KAN-119). Neither can take the route class:
# both set the session cookie on `Response`, which a replay would not restore.
# Both are already safe to repeat. The only exemptions: KAN-266 converted every
# other POST, and its not-yet-converted allowlist is gone. A new exemption is
# Anto's ruling by name.
EXEMPT = {
    "/v1/auth/logout": "deletes the session cookie; no tenant to key on, and a second logout changes nothing",
    "/v1/auth/switch-dealership": "its result is the new session cookie; switching to the same dealership again "
    "yields the same session",
    # KAN-196 (Anto, 2026-10-17): the five unauthenticated POSTs of the
    # identity front door. No tenant to key on before a session exists, and
    # login, the MFA step and invitation acceptance set the session cookie on
    # `Response`. The passkey variant of the MFA step is the same ruling: its
    # options call is a read with no side effect, its verify call mints the
    # session like the code-based verify does.
    "/v1/auth/login": "mints the session cookie; a retry with the same credentials simply signs in again",
    "/v1/auth/mfa/verify": "mints the session cookie; the challenge is single-use, so a replay is refused anyway",
    "/v1/auth/mfa/passkey/options": "the passkey leg of the MFA step: no side effect",
    "/v1/auth/mfa/passkey/verify": "the passkey leg of the MFA step: mints the session cookie, challenge single-use",
    "/v1/auth/password-reset/request": "no session, no tenant; the token issued consumes the previous one",
    "/v1/auth/password-reset/confirm": "the reset token is single-use, so a replay is refused by construction",
    "/v1/auth/invitations/accept": "the invitation token is single-use and the response sets the session cookie",
}


def _post_routes() -> dict[str, APIRoute]:
    return {path: route for path, route in api_routes(app.routes) if "POST" in route.methods}


def _published_posts() -> dict[str, dict]:
    return {path: item["post"] for path, item in app.openapi()["paths"].items() if "post" in item}


def test_the_walk_sees_every_post_the_api_publishes():
    walked = set(_post_routes())
    assert walked, "the walk found no POST route at all — it is not reaching into the included routers"
    assert walked == set(_published_posts())


def test_every_post_honours_the_idempotency_key():
    published = _published_posts()
    failures = []
    for path, route in sorted(_post_routes().items()):
        if path in EXEMPT:
            continue
        header = next(
            (
                p
                for p in published[path].get("parameters", [])
                if p["in"] == "header" and p["name"] == IDEMPOTENCY_KEY_HEADER
            ),
            None,
        )
        if not isinstance(route, IdempotentRoute):
            failures.append(f"POST {path}: not an IdempotentRoute — build its router with route_class=IdempotentRoute")
        elif header is None:
            failures.append(f"POST {path}: the OpenAPI does not declare {IDEMPOTENCY_KEY_HEADER}")
        elif header.get("required"):
            failures.append(f"POST {path}: {IDEMPOTENCY_KEY_HEADER} is required — a breaking contract change")
    assert not failures, "\n".join(failures)


def test_the_exemptions_carry_no_stale_entry():
    posts = _post_routes()
    for path in EXEMPT:
        assert path in posts, f"{path} is exempt but no longer a POST route — remove the entry"
        assert not isinstance(posts[path], IdempotentRoute), (
            f"POST {path} is an IdempotentRoute now — remove its exemption"
        )

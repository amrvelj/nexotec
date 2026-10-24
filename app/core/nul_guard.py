"""KAN-387: a NUL character (U+0000) in a request is a 422, never a 500.

Postgres `text` cannot hold NUL, so any request string that carries one and is
bound into a query raises `psycopg.DataError` — a 500 — on whichever route
binds it first. This guard refuses it once, before routing, for everything a
route can read a string from: the path, the query string and a JSON or
form-urlencoded body (keys and values, at any depth). Anto's ruling
(2026-10-19): refuse with the taxonomy's 422, never strip — what is stored is
always what was sent — and build it beside `CamelModel`, not in it.

Headers and cookies need no check: HTTP does not allow NUL in them, and the
server refuses such a request before it reaches the application. A body that
is neither JSON nor form-urlencoded (an uploaded image) is bytes, never a
string, and passes untouched.

`exempt_paths` names the routes whose refusal must look like every other
refusal (the login front door's uniform 401, FR-AU-07/16); such a route
handles a NUL itself.
"""

import json
from collections.abc import Iterable
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import UnprocessableEntityError

NUL = "\x00"

NUL_REFUSED = "The request contains a character that is not allowed (NUL, U+0000)."


def contains_nul(value: object) -> bool:
    """True when any string in a decoded JSON value — a key or a value, at
    any depth — holds a NUL. Iterative, so a deeply nested body cannot
    exhaust the stack."""

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if NUL in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def _query_has_nul(raw: bytes) -> bool:
    return any(NUL in key or NUL in value for key, value in parse_qsl(raw.decode("latin-1"), keep_blank_values=True))


def _content_type(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"content-type":
            return value.decode("latin-1").split(";", 1)[0].strip().lower()
    return ""


def _is_json(content_type: str) -> bool:
    # FastAPI decodes a body as JSON when it has no content type at all, or
    # an `application/json` / `…+json` one — the guard reads the same bodies.
    return not content_type or content_type == "application/json" or content_type.endswith("+json")


def _body_has_nul(content_type: str, body: bytes) -> bool:
    if not body:
        return False
    if content_type == "application/x-www-form-urlencoded":
        return _query_has_nul(body)
    if not _is_json(content_type):
        return False
    try:
        decoded = json.loads(body)
    except ValueError:
        return False  # not JSON: the route's own validation answers it (422)
    return contains_nul(decoded)


class NulCharacterGuard:
    """Pure ASGI middleware: refuses a request carrying a NUL with 422
    (`details.reason = "nul_character"`, `details.location` = where)."""

    def __init__(self, app: ASGIApp, exempt_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        if NUL in scope["path"]:
            await self._refuse(scope, receive, send, "path")
            return
        if _query_has_nul(scope.get("query_string", b"")):
            await self._refuse(scope, receive, send, "query")
            return

        content_type = _content_type(scope)
        if not (_is_json(content_type) or content_type == "application/x-www-form-urlencoded"):
            await self.app(scope, receive, send)
            return

        # Read the whole body, then hand the same bytes on: the route reads
        # them exactly as the client sent them.
        received: list[Message] = []
        while True:
            message = await receive()
            received.append(message)
            if message["type"] != "http.request":
                # The client went away before the body was complete: the
                # route sees exactly that.
                await self.app(scope, _replay(received, receive), send)
                return
            if not message.get("more_body", False):
                break
        body = b"".join(m.get("body", b"") for m in received)
        if _body_has_nul(content_type, body):
            await self._refuse(scope, receive, send, "body")
            return
        await self.app(scope, _replay([{"type": "http.request", "body": body, "more_body": False}], receive), send)

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, location: str) -> None:
        error = UnprocessableEntityError(NUL_REFUSED, details={"reason": "nul_character", "location": location})
        await error.to_response()(scope, receive, send)


def _replay(messages: list[Message], receive: Receive) -> Receive:
    pending = list(messages)

    async def replayed() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return replayed

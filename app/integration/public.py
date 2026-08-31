"""The only surface other contexts may import from `app.integration`.
Import-linter's contract allows `app.<other-context>` to import
`app.integration.public`, never `.models`/`.services`/`.api`/`.adapters`
directly.

Deliberately absent: anything that could return a secret value.
`resolve_secret`/`create_secret`/`update_secret`/`delete_secret` (services/
secrets_backend.py) are never exported here — only the adapters (PR-2/3),
which already live inside this context, may call them.

`call_capability` (PR-2), `get_entitlement` (PR-5) land here once those
PRs exist; this module ships now, empty of cross-context surface, because
nothing outside this context has a real caller yet (PR-1 is the registry
itself, not a consumer-facing gateway).
"""

from app.integration.models.connection import ConnectionStatus

__all__ = [
    "ConnectionStatus",
]

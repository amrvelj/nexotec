"""app.integration — the 12th bounded context (WP-6 PR-1).

"One registry, many gateways" (Integrations & API Credentials v0.1). This
context owns the generic parts every third-party connection needs
regardless of protocol: the connection registry, write-only secret
references (the actual material lives in Infisical, never here — see
services/secrets_backend.py), per-connection entitlements, the call log,
and (PR-6) retention/notification. It never speaks a provider protocol
itself — auto-i-dat's SOAP/AES specifics live in adapters/ (PR-2/3), still
inside this context (the spec's own diagram draws `provider-gateway` as
"no business data... never persists credentials of its own", which is
exactly this package's own posture) rather than inside `app.vehicle`. The
catalogue MIRROR — the actual per-tenant variant/option/colour/tyre
content — is `app.vehicle`'s own job (WP-6 PR-4); it calls
`app.integration.public.call_capability` the same way `app.sales` already
calls `app.inventory.public`.

PRD-Vehicles' own module-decomposition table names `provider-gateway` as
"the likeliest first extraction, on quota isolation" — the same reasoning
that earned `app.valuation` its own context in WP-8. Reciprocal
import-linter contract: only `app.integration.public` is reachable from
outside this package.
"""

# Importing this submodule registers the real auto-i-dat SOAP adapter
# factory with services/gateway.py (PR-3) as a side effect, the same way
# services/gateway.py's own module already registers the mock factory in
# its own dict literal. Importing app.integration anywhere (every context
# that reaches this package's public.py does) is enough to make the real
# provider resolvable — no separate app-startup wiring step needed. This
# does NOT import zeep eagerly: auto_i_dat_soap.py's own build_zeep_client
# imports it lazily, inside the function body, so this import costs
# nothing extra for tests/local dev that never construct a real adapter.
from app.integration.adapters import auto_i_dat_soap as _auto_i_dat_soap  # noqa: F401

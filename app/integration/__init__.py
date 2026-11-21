"""app.integration — the 12th bounded context (WP-6 PR-1).

"One registry, many gateways" (Integrations & API Credentials v0.1). This
context owns the generic parts every third-party connection needs
regardless of protocol: the connection registry, write-only secret
references (the actual material lives in Infisical, never here — see
services/secrets_backend.py), per-connection entitlements, the call log,
and (PR-6) retention/notification. It never speaks a provider protocol
itself — auto-i-dat's identAPI specifics live in adapters/ (KAN-488), still
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

# KAN-227 — the four AI provider adapters (OpenAI, Azure OpenAI, Mistral,
# Anthropic) behind `adapters/ai_base.py::AiGateway`, registered the same way
# as the two below; httpx opens nothing until a request is sent.
from app.integration.adapters import ai_providers as _ai_providers  # noqa: F401

# Importing this submodule registers the real auto-i-dat adapter factory
# with services/gateway.py as a side effect, the same way
# services/gateway.py's own module already registers the mock factory in
# its own dict literal. Importing app.integration anywhere (every context
# that reaches this package's public.py does) is enough to make the real
# provider resolvable — no separate app-startup wiring step needed.
# KAN-488 (ADR-111): `auto_i_dat` resolves to the identAPI adapter; the SOAP
# adapter and its legacy code `auto_i_dat_soap` are gone (KAN-550).
from app.integration.adapters import auto_i_dat_ident as _auto_i_dat_ident  # noqa: F401

# KAN-27 — same reasoning, for the AutoScout24 marketplace-transmission
# adapter. ftplib is a Python stdlib module, and build_real_ftp_client only
# opens a connection when a real adapter is actually constructed, never at
# import time.
from app.integration.adapters import autoscout24 as _autoscout24  # noqa: F401

# KAN-226 PR-2 — the Google Business Profile adapter (Nexotec's own OAuth
# client, one platform-scope connection); httpx opens nothing until a request
# is sent.
from app.integration.adapters import google_business_profile as _google_business_profile  # noqa: F401

# KAN-215 — the two mailbox adapters (Microsoft 365 Graph, Google Workspace
# Gmail) behind `adapters/mail_base.py::MailGateway`; httpx opens nothing
# until a request is sent.
from app.integration.adapters import mail_providers as _mail_providers  # noqa: F401

# KAN-217 PR-3 — the Betrieb's own SMS (Twilio) and WhatsApp (Meta Cloud API)
# accounts behind `adapters/messaging_base.py::MessagingGateway`; httpx opens
# nothing until a request is sent.
from app.integration.adapters import messaging_providers as _messaging_providers  # noqa: F401

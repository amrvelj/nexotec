---
paths:
  - "app/integration/**"
  - "alembic/versions/integration/**"
  - "tests/test_integration_*.py"
  - "tests/test_identapi_*.py"
  - "tests/identapi_*.py"
  - "tests/test_kan549_retire_dat_provider.py"
  - "tests/architecture/test_auto_i_dat_calls_go_through_the_gateway.py"
  - "tests/architecture/test_provider_adapter_is_the_identapi.py"
  - "tests/architecture/test_no_mcp_server.py"
  - "tests/architecture/test_no_real_provider_account_values.py"
  - "tests/architecture/test_capability_codes_are_in_the_vocabulary.py"
  - "frontend/apps/dms/src/**/*ntegration*"
  - "frontend/apps/dms/src/components/connectionRowMenu.tsx"
---
<!-- Maintainer note (stripped before Claude sees it). Summarises the Integrations & API Credentials
PRD, ADR-013/ADR-075, ADR-111 as amended, ADR-113, and the identAPI test agreement (§1-§12; it is
confidential and never quoted). Cut from vehicle.md by KAN-614 (2026-11-10) to the invariants;
vehicle.md was verified against main@73baf3f on 2026-10-25 - this cut was not re-verified. -->

# The provider gateway and the integration registry

## One registry, many gateways
- `integration` owns connections, write-only secret refs (`integration_secret_ref`, one row per
  slot; values live in the secrets manager, never in the app database), entitlements, the call log
  and retention. The adapters live beside the registry; the catalogue mirror is `vehicle`'s job,
  calling `integration.public.call_capability`.
- **Every auto-i-dat call goes through the gateway** (`test_auto_i_dat_calls_go_through_the_gateway.py`).
  Licensed data never travels through the cross-tenant shared identity response (FR-V-14).
- **`ProviderAdapter` is exactly the identAPI's operations** (`test_provider_adapter_is_the_identapi.py`):
  the identAPI adapter implements them plus its access check and probes; the demo provider
  (`auto_i_dat_mock`) implements exactly them over invented fixtures carrying auto-i's own codes and
  meters each request under the same operation name — demos and tests run on it.
- Bodies are read by `adapters/identapi_parse.py`, which interprets no code: `0` is *unknown*
  (NULL), a null `productionEnd` is *in production*.

## auto-i-dat is the identAPI (ADR-111)
- `auto_i_dat` resolves to `adapters/auto_i_dat_ident.py` (json:api over `httpx`), one choke point
  `_request`: one call-log row per attempt under the operation's own name; a timeout or 5xx retried
  once, a 4xx never; 401 → the token dropped and asked once more; 403 → `ProviderNotEntitledError`;
  400/422 → `ProviderRequestError`. A list call asks the first page only, never a second (the one
  bounded walk is the options of the one car opened, `OPTIONS_PAGE_CAP`).
- **Login** (`adapters/autoi_oauth.py`): metadata discovery from auto-i's well-known document
  (cached ≤ 24 h, **never hard-coded**) and the **User Enrollment Grant** naming the **acting
  Nexotec user** (`platform.public.get_active_user_acting_in`) with the service account's client id
  (config `clientId`) and secret (slot `client_secret`). The Bearer token is cached in process **per
  connection and per user**, keyed by the connection's `version` and `rotated_at`, never persisted,
  never logged (`access_token` / `Authorization` are redacted names). The enrolment's
  `Accept-Language` is the user's UI language as `de-CH`/`fr-CH`/`it-CH`/`en-CH`
  (`platform.public.ui_language_of`, KAN-585); not part of the cache key.
- A call with no signed-in user, no `clientId` or no secret is a `ProviderConfigurationError` — the
  dealer's to fix: no breaker charge, no invented row. A 400/422 or 403 is the provider's coherent
  answer, logged `ERROR` and **never charged to the breaker** (one un-entitled option must not
  switch off the others), nor is auto-i refusing to enrol one more user (`user_quota_exceeded` →
  `ProviderUserQuotaExceededError`, KAN-586: that user's answer, not the connection's); a transport
  failure, a 5xx and any other refused login (a wrong client secret) charge it.
- **«Test connection»** = discovery + token + captions + one documented probe per licence option
  (`ident_basis`, `ausstattung`, `bewertung`, `vin`, `pneu`, `energielabel`; 403 = not granted,
  200/404 = granted, anything else leaves the row), the clicking user as the actor; a refused login
  stops the probes and charges the breaker once (an over-quota user's does not). It is the identAPI's go-live check.
- What has run against the **real** service account (2026-11-07, outside the app): discovery, one
  token request, one caption call. Everything else ran against the mock or a fake transport — say
  so in any claim about real provider behaviour.

## Captions are vocabulary, global, fetched on «Test connection» and by staff (ADR-111)
`integration_provider_caption`, one row per (provider, group, list, code) with four labels; each
language committed as it lands; a failure leaves the connection `connected`. No scheduled refresh (a
background job has no user to enrol). **Staff see and refresh them** (KAN-588): every store records
`integration_provider_caption_fetch` (provider, language, `fetched_at`, code count; integration migration
`b1f58e30a656`, seeded from the captions); the catalogue's staff-only «Anbieter-Vokabular» tab
(`ProviderCaptionsPanel`) reads `GET /v1/integrations/captions/fetches` and «Vokabular aktualisieren»
is `POST /v1/integrations/captions/refresh` (`services/caption_refresh.py`, `require_access_role()`): the
four languages through the acting Betrieb's enabled vehicle-data connection, under the staff user's own
token, each committed as it lands, audited `captions_refreshed` on the connection; 409
`no_provider_connection` / `provider_not_configured` / `not_entitled` / `provider_has_no_captions`, 422
`provider_unavailable`, the provider's failures with `languagesFetched`. No job calls it. Others read them through `integration.public.get_captions` —
to show a provider's wording beside an unmapped code, **never to fill a reference list** (ADR-044).

## The licence (identAPI test agreement, confidential — Anto's Drive, never quoted)
- **Single lookups only** — see `vehicle-catalogue.md`.
- **Every provider request is counted** (§6): one `integration_call_log` row per request (a retry is
  a second row), the provider's operation name as `capability`, `cost_units = 1` — an adapter wraps
  each in `services/metering.py::provider_request`; the breaker stays per `call_capability` block.
  **Nexotec enforces no quota and sends no usage warning** (Anto: auto-i-dat limits its own access).
- **The test access never runs in production** (§1): it is a `sandbox` connection, and on any
  `DMS_ENVIRONMENT` but `development`, `test` or `staging` the gateway refuses every sandbox
  call before the provider (`SandboxConnectionOnProductionError`, audited). The value is one of
  `development` / `test` / `staging` / `production` (`app/core/deployment_environment.py`, KAN-499):
  an unknown name refuses to start, and so does an **unset** one outside pytest and local development
  (a `.env` in the working directory), where it reads as `development`; a name that does not parse
  reads as production in every reader (gateway, object store, mailer). The Connect dialog
  says: a service account only, never the personal token, test environments only. There are no
  licence types or end dates: a switched-off connection shows as failing.
- **VIN queries are not in the licence** (§8): the `vin` option is probed and its `granted = False`
  row — or no row — keeps the VIN rung from ever calling it. `integration.public.vin_decode_granted`
  reads that row strictly (no row = not granted, the opposite of every other option).
- **No MCP for provider data** (§3, the application; sessions reading staging through Render's
  connector are a tooling ruling, ADR-155): never exposed through an MCP server, tool manifest or
  agent-facing API (`test_no_mcp_server.py`, which also refuses an MCP dependency its
  `ALLOWED_MCP_DEPENDENCIES` does not name — empty: Anto's ruling by name). An AI prompt hook may use
  provider data only inside the tenant's own request and response, never as a tool the model can
  call to fetch more. `.claude/` is not the application.
- **The repository is public for now** (Anto, 2026-11-13; it returns to private), so **nothing §5
  or §12 covers enters it**: no specification excerpt, account value or specification example in a
  file, a PR, an issue or a commit message, nor pasted into a gist, another repo or chat. Fixtures
  and the mock use values that read as invented (`test_no_real_provider_account_values.py`, which
  names them by hash only); the probes' inputs are examples from auto-i's **publicly served**
  OpenAPI document. Code-to-meaning tables (the `auto_i_dat` code map) are vocabulary, not covered
  (Anto, 2026-11-13).

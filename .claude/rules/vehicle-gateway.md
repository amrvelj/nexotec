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
  never logged (`access_token` / `Authorization` are redacted names).
- A call with no signed-in user, no `clientId` or no secret is a `ProviderConfigurationError` — the
  dealer's to fix: no breaker charge, no invented row. A 400/422 or 403 is the provider's coherent
  answer, logged `ERROR` and **never charged to the breaker** (one un-entitled option must not
  switch off the others); a transport failure, a 5xx and a refused login charge it.
- **«Test connection»** = discovery + token + captions + one documented probe per licence option
  (`ident_basis`, `ausstattung`, `bewertung`, `vin`, `pneu`, `energielabel`; 403 = not granted,
  200/404 = granted, anything else leaves the row), the clicking user as the actor; a refused login
  charges the breaker once and stops the probes. It is the identAPI's go-live check.
- What has run against the **real** service account (2026-11-07, outside the app): discovery, one
  token request, one caption call. Everything else ran against the mock or a fake transport — say
  so in any claim about real provider behaviour.

## Captions are vocabulary, global, fetched on «Test connection» only (ADR-111)
`integration_provider_caption`, one row per (provider, group, list, code) with four labels; each
language committed as it lands; a failure leaves the connection `connected`. No scheduled refresh (a
background job has no user to enrol). Others read them through `integration.public.get_captions` —
to show a provider's wording beside an unmapped code, **never to fill a reference list** (ADR-044).

## The licence (identAPI test agreement, confidential — Anto's Drive, never quoted)
- **Single lookups only** — see `vehicle-catalogue.md`.
- **Every provider request is counted** (§6): one `integration_call_log` row per request (a retry is
  a second row), the provider's operation name as `capability`, `cost_units = 1` — an adapter wraps
  each in `services/metering.py::provider_request`; the breaker stays per `call_capability` block.
  **Nexotec enforces no quota and sends no usage warning** (Anto: auto-i-dat limits its own access).
- **The test access never runs in production** (§1): it is a `sandbox` connection, and on any
  `DMS_ENVIRONMENT` but `development`, `local`, `test` or `staging` the gateway refuses every sandbox
  call before the provider (`SandboxConnectionOnProductionError`, audited). **An unset variable reads
  as `development`** — so production must set it, or the refusal never fires. The Connect dialog
  says: a service account only, never the personal token, test environments only. There are no
  licence types or end dates: a switched-off connection shows as failing.
- **VIN queries are not in the licence** (§8): the `vin` option is probed and its `granted = False`
  row — or no row — keeps the VIN rung from ever calling it. `integration.public.vin_decode_granted`
  reads that row strictly (no row = not granted, the opposite of every other option).
- **No MCP for provider data** (§3): never exposed through an MCP server, tool manifest or
  agent-facing API (`test_no_mcp_server.py`, which also refuses an MCP dependency its
  `ALLOWED_MCP_DEPENDENCIES` does not name — empty: Anto's ruling by name). An AI prompt hook may use
  provider data only inside the tenant's own request and response, never as a tool the model can
  call to fetch more. `.claude/` is not the application.
- **Specification excerpts never leave this private repository** (§5, §12): never pasted into a
  public issue, gist, other repo or chat. Fixtures and the mock use values that read as invented,
  never an account value or a specification example (`test_no_real_provider_account_values.py`);
  the probes' inputs are examples from auto-i's **publicly served** OpenAPI document.

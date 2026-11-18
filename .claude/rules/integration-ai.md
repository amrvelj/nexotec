---
paths:
  - "app/integration/adapters/ai_*.py"
  - "app/integration/services/ai_*.py"
  - "app/integration/api/ai_prompts.py"
  - "app/integration/models/ai_prompt.py"
  - "app/inventory/services/listing_suggestion.py"
  - "tests/integration/test_ai_*.py"
  - "frontend/apps/dms/src/pages/AiAdministrationPage*.tsx"
  - "frontend/apps/dms/src/components/translations/AiPromptSeedsPanel.tsx"
  - "frontend/apps/dms/src/modules/stock/components/stock-detail/ListingTextSuggestion.tsx"
---

# The Betrieb's own AI provider (KAN-227, D-A-33)

- **Bring your own key, nothing else in v1.** Four providers in the registry, category `ai`:
  `openai`, `azure_openai`, `mistral`, `anthropic` — slot `api_key`, the model (Azure: `endpoint`,
  `deployment`, `apiVersion`) is the dealer's configuration, never a default of ours; no sandbox.
  Nexotec holds no AI key. One port, `adapters/ai_base.py::AiGateway` (one completion: system +
  user message in, text + tokens out), behind `call_capability` like every provider.
- **One request, one call-log row, with its tokens** (`metering.ProviderRequest.report_tokens` →
  `integration_call_log.tokens_in/out`); the usage endpoint sums them. **No retry** — a completion is
  billed. «Test connection» is one tiny completion (`AiGateway.verify`), no captions, no probes.
- **The Azure endpoint is checked before anything is sent** (`ai_providers.azure_endpoint`: https,
  `*.openai.azure.com` / `*.cognitiveservices.azure.com`, nothing after the host) — the server never
  calls a URL a dealer typed.
- **Prompts are the standard texts' pattern, in integration** (ADR-181: AI prompts and
  generation live in integration; `integration_ai_prompt`): seeds
  (tenant NULL, `platform_admin` through Translations under `ui_translations`) and the Betrieb's
  overrides (`ai_prompts`, the manager), «the default has changed» derived on read, back to the
  default = delete the override. Codes are a closed registry (`services/ai_prompt_registry.py`,
  `vehicle_listing` in v1); a token the code does not list is refused on save and on render; a
  missing value renders as «keine Angabe» — the seeds say never to invent. Seed rows are literals
  in migration `e7a2c91d4b60`, kept equal to `ai_prompt_seed.py` by `tests/integration/test_ai_prompts.py`.
- **`integration.public.generate` is the one door** (platform cannot call integration, so it is not
  in `platform.public`): connection (the enabled one most recently tested), prompt (asked → the
  Betrieb's correspondence language → DE; override over seed), render, one completion. 409
  `ai_not_connected` / `ai_provider_not_configured`, 422 `ai_provider_unavailable` (`failure`: our
  adapter's wording, never the provider's).
- **A suggestion, never a publication** (FR-A-38): Stock's «Text vorschlagen»
  (`POST …/listing-text-suggestions`, `marketplace_publishing`) stores nothing; the user edits it in
  the dialog and takes it into the description, saved like typing — asked first when the description
  holds text, never cut at its 4000 characters (taking over waits until it fits; Anto to rule).
- **Licence §3:** provider data a caller puts into a prompt travels in the tenant's own request
  only; the port has no tool, function-calling or retrieval surface, and none may be added.
  Condition flags (`eigenschaften`, an accident history) are never sent.

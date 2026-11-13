---
paths:
  - "frontend/apps/dms/src/i18n/**"
  - "frontend/apps/dms/src/hooks/use*Labels*"
  - "frontend/apps/dms/src/hooks/useInlineEditRefusal.ts"
  - "frontend/apps/dms/src/utils/apiRefusal*"
  - "frontend/apps/dms/src/utils/vehiclePartyRefusal.ts"
  - "frontend/apps/dms/src/utils/providerText*"
  - "frontend/apps/dms/src/referenceLists.ts"
  - "frontend/apps/dms/src/utils/referenceData*"
  - "frontend/apps/dms/src/hooks/useReference*"
  - "frontend/apps/dms/src/hooks/useCountryOptions.ts"
  - "frontend/apps/dms/src/components/inlineEditRefusal*"
  - "frontend/apps/dms/src/architecture/no-api-error-message-render*"
  - "frontend/apps/dms/src/architecture/language-endonyms*"
  - "frontend/packages/ui-kit/src/kitLabels*"
  - "frontend/packages/ui-kit/src/test/frenchLabels.ts"
  - "scripts/split_locale_bundles.py"
---
<!-- Maintainer note. UI/UX Specification (i18n row), ADR-044; Anto's rulings on KAN-104, KAN-171,
KAN-343, KAN-398. Split out of ui.md by KAN-615 (2026-11-13). Last verified against main@73baf3f
(2026-10-25); the bundle layout with KAN-616's code. -->

# Translations (i18n)

- **Bundles:** `frontend/apps/dms/src/i18n/locales/{de,en,fr,it}/<namespace>.json`, one file per
  top-level namespace. A new key goes into its screen's namespace file in all four languages at
  once; a new namespace is four files plus `python3 scripts/split_locale_bundles.py index`, which
  writes the generated `locales/<language>.ts`. `localeKeyParity.test.ts` fails on a key or file a
  language lacks, a stale `<language>.ts`, or a file above 25'000 bytes (split it:
  `<namespace>.<key>.json`).
- **A key missing from every bundle renders `⚠ MISSING I18N KEY: <key>`**; one missing only from
  the active locale falls back to English, never German — no marker when English has the text
  (Anto, KAN-104). No hardcoded user-visible string, labels in dialogs and wizards included.
- **A kit component ships no user-visible string**, accessible names included: it takes required
  labels, so `tsc` refuses a caller without translations — the app's `use…Labels()` hooks
  (`useDataGridLabels`, `useColumnPanelLabels`, `useViewsAndFiltersLabels`, `useActionBarLabels`,
  …), a badge's `label`, `OverlayProvider`'s `closeLabel`, a `Picker`'s create path and a
  `DocumentPreview` download with their labels. A saved filter's text is one translated template
  per condition, never field + condition + value glued in the kit. `kitLabels*.render.test.tsx`
  renders each with French labels and refuses any former English default.
- **The one exception: the four language endonyms** (Anto, KAN-343): where a language names
  itself for every user (the reference-data screen's language fields, a customer's correspondence
  language) it reads «Deutsch», «Français», «Italiano», «English» from the one list
  `i18n/languageEndonyms.ts` (`architecture/language-endonyms-one-list.test.ts`). The UI-language
  picker and the per-language editors still translate the names.
- **An API refusal is shown in the UI language** (Anto, KAN-398), never `err.message` (the API's
  English, for logs only). The one helper is `utils/apiRefusal.ts::apiRefusalText(t, err,
  fallbackKey)`: a body-less response is `common.requestFailed`; then a stale If-Match 409; a known
  `details.reason` (its table — `apiRefusal.test.ts` fails on a `"reason": "…"` in `app/` it does
  not translate; a value the details carry goes through `REASON_PARAMS`); a 403, 404 and schema 422
  by status; anything else, a business rule refused without a reason included, is the screen's own
  key — so give such a refusal a `details.reason`. A screen with its own wording for a reason
  (merge, contract confirmation, business data) keeps its helper.
  `src/architecture/no-api-error-message-render.test.ts` refuses a `.message` render in the app and
  the kit (its allow-list: `ConfiguratorOverlay`'s `HostCommitError`, `FormDialog`'s
  existing-record notice, both built from `t(...)`).
- **DE, FR and IT person nouns use the generic masculine** (Anto, KAN-171; the spec's i18n row):
  "Verkaufsberater", "Conseiller de vente" — no slash, pair, parenthesis or point-médian form.
- Canonical reference data is translated by us; provider option text is stored and rendered **as
  delivered**, never translated (ADR-044).
- Loading the bundles, the runtime UI strings and a new user's starting language:
  `ui-i18n-runtime.md`.

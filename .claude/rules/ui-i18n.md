---
paths:
  - "frontend/apps/dms/src/i18n/**"
  - "frontend/apps/dms/src/main.tsx"
  - "frontend/apps/dms/src/hooks/use*Labels*"
  - "frontend/apps/dms/src/hooks/useUiPreferences.ts"
  - "frontend/apps/dms/src/hooks/UiPreferencesContext.tsx"
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
  - "frontend/apps/dms/src/architecture/main-waits-for-i18n*"
  - "frontend/packages/ui-kit/src/kitLabels*"
  - "frontend/packages/ui-kit/src/test/frenchLabels.ts"
  - "scripts/split_locale_bundles.py"
  - "app/platform/services/user_preference.py"
  - "app/platform/api/user_preferences.py"
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
- **Only `en` (the fallback) ships with the app**; `de`, `fr`, `it` are chunks fetched when first
  needed. i18next starts in the `ui` preference mirror's language (`storedUiLanguage`, else `de`)
  and `main.tsx` paints only once `i18nReady` resolves, so nobody sees another language first
  (`architecture/main-waits-for-i18n.test.ts`); a switch waits for its bundle. A bundle that cannot
  be fetched is asked for twice more, then the language shows in English and the next switch
  fetches again — no reload screen. No app code reads a non-active bundle.
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
- **The platform's published UI strings override the bundles at runtime** (FR-A-30…32):
  `i18n/runtimeTranslations.ts`, started by `main.tsx`, fetches `GET /v1/i18n/{language}` beside
  the bundle; the first paint waits for both, at most `FIRST_PAINT_BUDGET_MS` for the map; a switch
  waits for that language's map and asks again (ETag). Resources are rebuilt as bundle + published
  values, never patched. A key that renders the marker is reported, batched, to
  `POST /v1/i18n/missing-translations`. Backend: `platform-notifications.md`.
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
- **A new user starts in the home Betrieb's correspondence language** (Anto, KAN-104):
  `GET /v1/me/preferences/ui` for a user who never saved the scope answers
  `{"uiLanguage": <the Betrieb's default_correspondence_language>}` with `updatedAt: null` (a
  stored `ui` row without `uiLanguage` gets it on read) — a starting value the server does not
  store; the first save of any `ui` field stores it as the user's own. The platform mail texts and
  the push title follow it (`user_preference.ui_language_of`). Only the starting value is shared
  (CLAUDE.md, i18n); `useUiPreferences.ts` keeps `'de'` as the offline fallback.

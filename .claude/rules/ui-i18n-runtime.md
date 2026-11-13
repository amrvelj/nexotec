---
paths:
  - "frontend/apps/dms/src/main.tsx"
  - "frontend/apps/dms/src/i18n/index.ts"
  - "frontend/apps/dms/src/i18n/runtimeTranslations*"
  - "frontend/apps/dms/src/i18n/onDemandBundles*"
  - "frontend/apps/dms/src/hooks/useUiPreferences.ts"
  - "frontend/apps/dms/src/hooks/UiPreferencesContext.tsx"
  - "frontend/apps/dms/src/architecture/main-waits-for-i18n*"
  - "app/platform/services/user_preference.py"
  - "app/platform/api/user_preferences.py"
  - "tests/test_user_preference.py"
---
<!-- Maintainer note. FR-A-30…32; Anto's ruling on KAN-104 (decision 1 B). Split out of ui.md (KAN-615,
2026-11-13). Last verified against main@73baf3f (2026-10-25). -->

# Loading translations at runtime

- **Only `en` (the fallback) ships with the app**; `de`, `fr`, `it` are chunks fetched when first
  needed. i18next starts in the `ui` preference mirror's language (`storedUiLanguage`, else `de`)
  and `main.tsx` paints only once `i18nReady` resolves, so nobody sees another language first
  (`architecture/main-waits-for-i18n.test.ts`); a switch waits for its bundle. A bundle that cannot
  be fetched is asked for twice more, then the language shows in English and the next switch
  fetches again — no reload screen. No app code reads a non-active bundle.
- **The platform's published UI strings override the bundles at runtime** (FR-A-30…32):
  `i18n/runtimeTranslations.ts`, started by `main.tsx`, fetches `GET /v1/i18n/{language}` beside
  the bundle; the first paint waits for both, at most `FIRST_PAINT_BUDGET_MS` for the map; a switch
  waits for that language's map and asks again (ETag). Resources are rebuilt as bundle + published
  values, never patched. A key that renders the marker is reported, batched, to
  `POST /v1/i18n/missing-translations`. Backend: `platform-notifications.md`.
- **A new user starts in the home Betrieb's correspondence language** (Anto, KAN-104):
  `GET /v1/me/preferences/ui` for a user who never saved the scope answers
  `{"uiLanguage": <the Betrieb's default_correspondence_language>}` with `updatedAt: null` (a
  stored `ui` row without `uiLanguage` gets it on read) — a starting value the server does not
  store; the first save of any `ui` field stores it as the user's own. The platform mail texts and
  the push title follow it (`user_preference.ui_language_of`). Only the starting value is shared
  (CLAUDE.md, i18n); `useUiPreferences.ts` keeps `'de'` as the offline fallback.

---
paths:
  - "frontend/**/*.test.ts"
  - "frontend/**/*.test.tsx"
  - "frontend/apps/dms/src/test/**"
  - "frontend/apps/dms/src/modules/*/test/**"
  - "frontend/packages/ui-kit/src/test/**"
  - "frontend/apps/dms/vite*.config.*"
  - "frontend/packages/ui-kit/vite*.config.*"
---
<!-- Maintainer note. Moved out of tests.md and ui.md by KAN-615 (2026-11-13). Last verified
against main@73baf3f (2026-10-25). -->

# Frontend tests

- Render tests opt into jsdom per file (`// @vitest-environment jsdom`; the default is `node`)
  and are named `*.render.test.tsx` — not always beside the component: find them with
  `git grep -l <Component> -- '*.render.test.tsx'` before assuming a change can only be checked
  by eye. A change to a component that has one updates it in the same PR.
- In `frontend/apps/dms` a jsdom test never reaches the network: its default `fetch` never
  settles and the test fails naming the request unless it calls `installFakeBackend`
  (`src/test/fakeBackend.ts`).
- **Mantine is mounted with `testTheme`** (`src/test/testTheme.ts`: reduced motion, so no
  transition timer lands after jsdom is gone), through `renderWithProviders` or the test's own
  `<MantineProvider theme={testTheme}>`; `render-tests-mount-mantine-with-test-theme.test.ts`
  refuses any other use of the provider and a `testTheme` imported from elsewhere. The shared
  teardown drains React's queued Scheduler work (`src/test/setup.ts`). The ui-kit does the same
  with its own `testTheme`, a setup whose `matchMedia` reports reduced motion and
  `renderTestsMountMantineWithTestTheme.test.ts`; it has no teardown drain.
- **i18n in tests:** `src/test/setup.ts` waits for `i18nReady`, so every test starts in German. A
  test that reads `fr`/`it` text through `getFixedT` or `getResource` before anything switched to
  it calls `await i18n.loadLanguages(lng)` first. Tests never start the runtime translations
  (no network); `runtimeTranslations.render.test.tsx` does, and resets them. Keys:
  `localeKeyParity.test.ts`.

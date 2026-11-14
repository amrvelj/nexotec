import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// The default `environment` stays `node` — every pure-function test
// (format, valuationRowMenu, persistedPreferenceStorage, localeKeyParity,
// stockGroupColumns, the colour scan) keeps running with no DOM overhead.
// A render test opts into jsdom per file with a `// @vitest-environment
// jsdom` docblock; `setupFiles` then wires jest-dom's matchers and RTL
// auto-cleanup. This keeps CI's single `npx vitest run` step intact — no
// new lane, no second runner.
// A module's door (src/modules/<module>/index.ts, ADR-161) holds re-exports
// and nothing else, so it has no side effects of its own — which the bundler
// cannot prove, because the files it re-exports from are ordinary modules.
// Without this, importing one export from a door makes the door's chunk
// import every file the door names, and a screen that renders one of a
// module's components loads all of them (KAN-636 step 2b, measured: the
// valuation create dialog's load grew 15%). Every other module keeps the
// bundler's default; module-boundaries.test.ts holds a door to re-exports.
// The limit (KAN-636 review): a module a door re-exports whose exports go
// unused through the door is not evaluated in the build, while vitest and the
// dev server evaluate it — so a door-exported file must not depend on a
// side-effect import (a CSS file, a registration) of its own; none does today.
// The pattern names this app's own doors only, never a dependency's.
const DOOR = /\/apps\/dms\/src\/modules\/[^/]+\/index\.ts$/

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      treeshake: { moduleSideEffects: (id) => (DOOR.test(id) ? false : undefined) },
    },
  },
  test: {
    environment: 'node',
    setupFiles: ['./src/test/setup.ts'],
    // A concrete origin so `localStorage` works in jsdom (the default
    // `about:blank` is an opaque origin where storage throws) and the
    // MemoryRouter isn't the only thing standing between a test and a real
    // URL. Only affects files that opt into the jsdom environment.
    environmentOptions: { jsdom: { url: 'http://localhost/' } },
    // KAN-325 — one timeout for the whole project; no describe or test sets
    // its own. A render test mounts a whole Mantine page in React's
    // development build on jsdom, so its cost is CPU and scales with the
    // machine's load. Measured on the 4-core cloud machine, slowest test
    // (ConfiguratorOverlay.coded "offers all five"): 3.6 s alone, 7 s with
    // four workers on idle cores, 12 s with other builders' suites on the
    // same cores (load average 7–11). 30 s is two and a half times that
    // worst case: a test fails for being wrong, never for being queued. A
    // test that needs more is a test to profile, not a timeout to raise.
    testTimeout: 30_000,
  },
})

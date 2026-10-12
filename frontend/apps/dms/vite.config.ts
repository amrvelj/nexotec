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
export default defineConfig({
  plugins: [react()],
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

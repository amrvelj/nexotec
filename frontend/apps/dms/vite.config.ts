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
  },
})

import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// ui-kit ships pure logic (column layout, sorting, filter predicates) AND
// React components. The default `environment` stays `node` so the pure
// logic tests (columnLayout, sorting, filterPredicate, savedView,
// overlayStack) keep running with no DOM overhead; a component test opts
// into jsdom per file with a `// @vitest-environment jsdom` docblock. This
// is deliberately NOT `environmentMatchGlobs` — that option was removed in
// vitest 4 — and NOT a second `projects` entry, which would fan the one
// `npx vitest run` CI step into two.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'node',
    setupFiles: ['./src/test/setup.ts'],
    environmentOptions: { jsdom: { url: 'http://localhost/' } },
  },
})

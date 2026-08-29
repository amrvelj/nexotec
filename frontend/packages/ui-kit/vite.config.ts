import { defineConfig } from 'vitest/config'

// ui-kit ships pure logic (column layout, sorting, filter predicates) as
// well as components — this config exists so that logic has somewhere to
// be tested from its own package, rather than every consumer app having to
// re-test another package's internals. `environment: 'node'` matches the
// dms app's own vite.config.ts; nothing here renders a component yet.
export default defineConfig({
  test: {
    environment: 'node',
  },
})

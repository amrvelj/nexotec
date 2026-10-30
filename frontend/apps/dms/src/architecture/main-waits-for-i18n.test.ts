import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// KAN-438 — French and Italian load on demand, so a user whose last language was
// one of them would see German until the bundle arrived if the app painted at
// once. main.tsx renders only inside `i18nReady.then(...)`, exactly once.
const MAIN = join(dirname(fileURLToPath(import.meta.url)), '..', 'main.tsx')

describe('the first paint waits for the start language (KAN-438)', () => {
  it('main.tsx creates the root only inside i18nReady.then', () => {
    const main = readFileSync(MAIN, 'utf8')
    const ready = main.indexOf('i18nReady.then(')
    expect(ready).toBeGreaterThan(-1)
    expect(main.match(/createRoot\(/g)).toHaveLength(1)
    expect(main.indexOf('createRoot(')).toBeGreaterThan(ready)
  })
})

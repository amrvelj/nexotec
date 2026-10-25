/// <reference types="node" />
// Node-only file in a browser-typed project — see no-hardcoded-colour.test.ts
// for why this one reference is the right opt-in.
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// KAN-37 (ADR-101): the Places API policies let us keep the address the
// advisor picked only because the SUGGESTION LIST is never persisted or
// logged — not in our call log, not in an audit row, not in a debug trace.
// The browser module that talks to Google therefore imports no API client
// (nothing it sees can reach our backend), writes nothing to the console or
// to browser storage, and talks to Google's origin alone. The component
// that renders the suggestions keeps them in React state and hands the
// caller only the resolved address.

const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel: string) => readFileSync(join(SRC_ROOT, rel), 'utf8')

describe('the Google Places module reaches nothing but Google (KAN-37)', () => {
  const places = read('utils/googlePlaces.ts')
  const field = read('components/AddressSearchField.tsx')

  it('imports no API client and no hook that could carry a suggestion to the backend', () => {
    expect(places).not.toMatch(/from ['"]\.\.\/api\//)
    expect(places).not.toMatch(/import .* from ['"]react/)
    expect(places).not.toMatch(/@tanstack/)
  })

  it('writes nothing to the console or to browser storage', () => {
    for (const source of [places, field]) {
      expect(source).not.toMatch(/console\./)
      expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB/)
    }
  })

  it('calls one origin, and asks the details call for address components only', () => {
    const origins = [...places.matchAll(/https?:\/\/[^'"`/]+/g)].map((m) => m[0])
    expect(new Set(origins)).toEqual(new Set(['https://places.googleapis.com']))
    expect(places).toMatch(/'X-Goog-FieldMask': 'addressComponents'/)
    // The canton is never read from Google (D-13).
    expect(places).not.toMatch(/first\([^)]*administrative_area_level_1/)
  })

  it('the search field persists no suggestion: it only writes the resolved address through onSelect', () => {
    expect(field).not.toMatch(/api\.(post|patch|put)/)
    expect(field).toMatch(/onSelect\(address\)/)
  })
})

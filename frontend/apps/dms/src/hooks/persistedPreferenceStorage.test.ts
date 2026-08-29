import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readLocalMirror, writeLocalMirror } from './persistedPreferenceStorage'

// This project's vitest runs with `environment: 'node'` (vite.config.ts),
// so there is no real `localStorage` global — a tiny in-memory stand-in is
// enough to exercise these two functions' own logic.
function installFakeLocalStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => void store.set(key, value),
    removeItem: (key: string) => void store.delete(key),
  })
  return store
}

interface Payload {
  schemaVersion: number
  density: string
}

const DEFAULTS: Payload = { schemaVersion: 1, density: 'default' }

describe('readLocalMirror', () => {
  beforeEach(() => {
    installFakeLocalStorage()
  })

  it('returns the defaults when nothing is stored', () => {
    expect(readLocalMirror('some-key', DEFAULTS)).toEqual(DEFAULTS)
  })

  it('merges a stored partial payload over the defaults', () => {
    writeLocalMirror('some-key', { density: 'comfortable' })
    expect(readLocalMirror('some-key', DEFAULTS)).toEqual({ schemaVersion: 1, density: 'comfortable' })
  })

  it('falls back to the defaults on corrupted JSON rather than throwing', () => {
    localStorage.setItem('some-key', '{not valid json')
    expect(readLocalMirror('some-key', DEFAULTS)).toEqual(DEFAULTS)
  })

  it('falls back to the defaults when localStorage itself throws (private mode, quota)', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('access denied')
      },
    })
    expect(readLocalMirror('some-key', DEFAULTS)).toEqual(DEFAULTS)
  })
})

describe('writeLocalMirror', () => {
  beforeEach(() => {
    installFakeLocalStorage()
  })

  it('round-trips through readLocalMirror', () => {
    writeLocalMirror('some-key', { schemaVersion: 1, density: 'compact' })
    expect(readLocalMirror('some-key', DEFAULTS)).toEqual({ schemaVersion: 1, density: 'compact' })
  })

  it('never throws even when the underlying store does', () => {
    vi.stubGlobal('localStorage', {
      setItem: () => {
        throw new Error('quota exceeded')
      },
    })
    expect(() => writeLocalMirror('some-key', DEFAULTS)).not.toThrow()
  })
})

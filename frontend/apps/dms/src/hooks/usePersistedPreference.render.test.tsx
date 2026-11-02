// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { installFakeBackend } from '../test/fakeBackend'
import { usePersistedPreference } from './usePersistedPreference'

// KAN-531 — a write still waiting on its 500 ms debounce when the screen
// unmounts is sent at once, under the scope it was made for. Before, the
// uncleared timer fired after the screen had gone: in the suite, into the
// next test (StockListPage.dateOnly's group test failed on the stray grid
// PUT); in the app, 500 ms after a route change — and a scope change within
// the debounce dropped the earlier scope's write altogether. (Closing the tab
// runs no cleanup: a write pending then is still lost — KAN-532.)

interface Prefs {
  schemaVersion: number
  density: string
}
const DEFAULTS: Prefs = { schemaVersion: 1, density: 'normal' }
const puts = (backend: ReturnType<typeof installFakeBackend>, scope: string) => backend.callsTo(new RegExp(`/me/preferences/${scope}$`), 'PUT')

describe('usePersistedPreference — a pending write is never left behind (KAN-531)', () => {
  it('sends the pending write when the screen unmounts, without waiting for the debounce', () => {
    const backend = installFakeBackend([])
    const { result, unmount } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))

    act(() => result.current.update({ density: 'compact' }))
    expect(puts(backend, 'grid:test')).toHaveLength(0) // still debounced while mounted

    unmount()
    const sent = puts(backend, 'grid:test')
    expect(sent).toHaveLength(1)
    expect(sent[0].body).toEqual({ schemaVersion: 1, density: 'compact' })
  })

  it('sends nothing on unmount when no write is pending', async () => {
    const backend = installFakeBackend([])
    const { result, unmount } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    await act(() => new Promise((resolve) => setTimeout(resolve, 600)))
    expect(puts(backend, 'grid:test')).toHaveLength(1) // the debounced write went out

    unmount()
    expect(puts(backend, 'grid:test')).toHaveLength(1)
  })

  it('sends a pending write under its own scope when the scope changes', () => {
    const backend = installFakeBackend([])
    const { result, rerender } = renderHook(({ scope }) => usePersistedPreference<Prefs>(scope, `mirror-${scope}`, DEFAULTS), {
      initialProps: { scope: 'grid:first' },
    })
    act(() => result.current.update({ density: 'compact' }))
    rerender({ scope: 'grid:second' })
    expect(puts(backend, 'grid:first')).toHaveLength(1)
    expect(puts(backend, 'grid:second')).toHaveLength(0)
  })
})

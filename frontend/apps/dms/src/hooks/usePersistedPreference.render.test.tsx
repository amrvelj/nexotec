// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { installFakeBackend, status } from '../test/fakeBackend'
import { usePersistedPreference } from './usePersistedPreference'

// KAN-531 — a write still waiting on its 500 ms debounce when the screen
// unmounts is sent at once, under the scope it was made for. Before, the
// uncleared timer fired after the screen had gone: in the suite, into the
// next test (StockListPage.dateOnly's group test failed on the stray grid
// PUT); in the app, 500 ms after a route change — and a scope change within
// the debounce dropped the earlier scope's write altogether.
//
// KAN-532 — closing or reloading the tab runs no React cleanup, so a write
// still pending then was lost: the mirror had it, the server did not, and on
// the next load the server's GET won. The page being hidden (`pagehide`, or
// `visibilitychange` to hidden) now sends it at once, with `keepalive` so the
// browser lets it outlive the page.

interface Prefs {
  schemaVersion: number
  density: string
}
const DEFAULTS: Prefs = { schemaVersion: 1, density: 'normal' }
function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
}
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

describe('usePersistedPreference — a pending write survives the tab closing (KAN-532)', () => {
  it('sends the pending write with keepalive when the page is hidden (pagehide)', async () => {
    const backend = installFakeBackend([])
    const { result, unmount } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))

    act(() => result.current.update({ density: 'compact' }))
    expect(puts(backend, 'grid:test')).toHaveLength(0)

    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    const sent = puts(backend, 'grid:test')
    expect(sent).toHaveLength(1)
    expect(sent[0].body).toEqual({ schemaVersion: 1, density: 'compact' })
    expect(sent[0].keepalive).toBe(true)

    // Sent once: neither the debounce nor the unmount sends it again.
    await act(() => new Promise((resolve) => setTimeout(resolve, 600)))
    expect(puts(backend, 'grid:test')).toHaveLength(1)
    unmount()
    expect(puts(backend, 'grid:test')).toHaveLength(1)
  })

  it('sends the pending write with keepalive when the document turns hidden', () => {
    const backend = installFakeBackend([])
    const { result } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    try {
      setVisibility('hidden')
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
    } finally {
      setVisibility('visible')
    }
    const sent = puts(backend, 'grid:test')
    expect(sent).toHaveLength(1)
    expect(sent[0].keepalive).toBe(true)
  })

  it('keeps the debounce while the page stays visible, and sends nothing on hide when nothing is pending', async () => {
    const backend = installFakeBackend([])
    const { result } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    act(() => {
      document.dispatchEvent(new Event('visibilitychange')) // still visible: no flush
    })
    expect(puts(backend, 'grid:test')).toHaveLength(0)
    await act(() => new Promise((resolve) => setTimeout(resolve, 600)))
    const sent = puts(backend, 'grid:test')
    expect(sent).toHaveLength(1)
    expect(sent[0].keepalive).toBe(false) // an ordinary debounced write

    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    expect(puts(backend, 'grid:test')).toHaveLength(1)
  })

  it('a write made after a scope change goes out under the new scope only when the page is hidden', () => {
    // The listeners of the scope that was left are gone: one still attached
    // would read the shared pending write and send it under the old scope.
    const backend = installFakeBackend([])
    const { result, rerender } = renderHook(({ scope }) => usePersistedPreference<Prefs>(scope, `mirror-${scope}`, DEFAULTS), {
      initialProps: { scope: 'grid:first' },
    })
    rerender({ scope: 'grid:second' })
    act(() => result.current.update({ density: 'compact' }))
    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    expect(puts(backend, 'grid:first')).toHaveLength(0)
    const sent = puts(backend, 'grid:second')
    expect(sent).toHaveLength(1)
    expect(sent[0].keepalive).toBe(true)
  })

  it('sends the write again without keepalive when the browser refuses the keepalive request', async () => {
    // The browser rejects a keepalive fetch whose body would pass its 64 KiB
    // keepalive budget (shared by every keepalive request in flight); the
    // server accepts up to 64 KiB per scope, so such a write is legal.
    const backend = installFakeBackend([])
    const delivered = vi.mocked(fetch).getMockImplementation()!
    const refused: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.keepalive) {
          refused.push(String(input))
          return Promise.reject(new TypeError('Failed to fetch: keepalive request body too large'))
        }
        return delivered(input, init)
      }),
    )
    const { result } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    await vi.waitFor(() => expect(puts(backend, 'grid:test')).toHaveLength(1))
    expect(refused).toHaveLength(1)
    const sent = puts(backend, 'grid:test')
    expect(sent[0].keepalive).toBe(false)
    expect(sent[0].body).toEqual({ schemaVersion: 1, density: 'compact' })
  })

  it('does not resend a write the server refused', async () => {
    const backend = installFakeBackend([
      { method: 'PUT', match: /\/me\/preferences\/grid:test$/, handler: () => status(422, { error: { code: 'validation_error', message: 'too big' } }) },
    ])
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { result } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    await vi.waitFor(() => expect(error).toHaveBeenCalled())
    expect(puts(backend, 'grid:test')).toHaveLength(1)
    error.mockRestore()
  })

  it('stops listening once the screen has gone', () => {
    const backend = installFakeBackend([])
    const { result, unmount } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    unmount() // KAN-531: sent at once
    act(() => {
      window.dispatchEvent(new Event('pagehide'))
    })
    expect(puts(backend, 'grid:test')).toHaveLength(1)
  })
})

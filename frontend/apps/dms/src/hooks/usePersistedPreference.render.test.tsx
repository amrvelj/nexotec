// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { installFakeBackend, status } from '../test/fakeBackend'
import { usePersistedPreference } from './usePersistedPreference'
import { readLocalMirror } from './persistedPreferenceStorage'

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

    // The same for the document turning hidden.
    act(() => result.current.update({ density: 'normal' }))
    try {
      setVisibility('hidden')
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'))
      })
    } finally {
      setVisibility('visible')
    }
    expect(puts(backend, 'grid:first')).toHaveLength(0)
    expect(puts(backend, 'grid:second')).toHaveLength(2)
  })

  it('does not resend a failed keepalive write once a newer write for the scope has been sent', async () => {
    // A rejected fetch cannot say whether its request reached the server, and
    // the PUT is last-write-wins: resending the older write after a newer
    // one would put the older value back on the server.
    const backend = installFakeBackend([])
    const delivered = vi.mocked(fetch).getMockImplementation()!
    let failKeepalive: (reason: unknown) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.keepalive) return new Promise<Response>((_, reject) => (failKeepalive = reject))
        return delivered(input, init)
      }),
    )
    const { result } = renderHook(() => usePersistedPreference<Prefs>('grid:test', 'test-grid', DEFAULTS))
    act(() => result.current.update({ density: 'compact' }))
    act(() => {
      window.dispatchEvent(new Event('pagehide')) // A: keepalive, hangs
    })
    act(() => result.current.update({ density: 'comfortable' }))
    await act(() => new Promise((resolve) => setTimeout(resolve, 600))) // B: debounced, delivered
    expect(puts(backend, 'grid:test').map((c) => c.body)).toEqual([{ schemaVersion: 1, density: 'comfortable' }])

    await act(async () => {
      failKeepalive(new TypeError('Failed to fetch'))
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(puts(backend, 'grid:test').map((c) => c.body)).toEqual([{ schemaVersion: 1, density: 'comfortable' }])
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
})

// KAN-104 item 2 — the server's answer (a new user's starting language, say)
// becomes the browser's mirror, so the next load's first frame already paints
// with it, not with the defaults or this browser's last user. Nothing is PUT.
describe('usePersistedPreference — the server answer is the next first frame (KAN-104)', () => {
  it('writes the server payload to the local mirror without saving it back', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /\/me\/preferences\/ui$/, handler: () => ({ scope: 'ui', payload: { density: 'compact' }, updatedAt: null }) },
    ])
    const { result } = renderHook(() => usePersistedPreference<Prefs>('ui', 'test-ui-mirror', DEFAULTS))

    await vi.waitFor(() => expect(result.current.value.density).toBe('compact'))

    expect(readLocalMirror<Prefs>('test-ui-mirror', DEFAULTS).density).toBe('compact')
    expect(puts(backend, 'ui')).toHaveLength(0)
  })
})

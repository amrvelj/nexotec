import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import { readLocalMirror, writeLocalMirror } from './persistedPreferenceStorage'

const WRITE_DEBOUNCE_MS = 500

/**
 * § User-Level Preference Persistence — the generic mechanics under EVERY
 * `/v1/me/preferences/{scope}` consumer: an optimistic local mirror read
 * synchronously on mount ("the grid paints in the correct layout on the
 * very first frame... the server is the source of truth"), a debounced
 * 500ms PUT, server-wins-on-conflict once the GET resolves, and "never
 * block: a failed preference write is logged, never surfaces an error
 * toast." This is `useUiPreferences.ts`'s own original implementation,
 * generalized over an arbitrary `scope`/payload shape instead of the one
 * hardcoded `ui` scope it was written against — `useUiPreferences` and the
 * newer `useGridPreferences`/`useSavedViews` all build on this one hook
 * rather than each re-implementing the same debounce/mirror machinery.
 */
export function usePersistedPreference<T extends { schemaVersion: number }>(
  scope: string,
  storageKey: string,
  defaults: T
): { value: T; update: (patch: Partial<T>) => void } {
  const [value, setValue] = useState<T>(() => readLocalMirror(storageKey, defaults))
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // KAN-531 — the write the timer will send, so an unmount (or a scope
  // change) can send it at once instead of leaving it on the timer.
  const pendingWrite = useRef<T | null>(null)

  useEffect(() => {
    // Sends a write still waiting on its debounce at once, under this scope.
    const flushPending = (keepalive: boolean) => {
      if (!writeTimer.current) return
      clearTimeout(writeTimer.current)
      writeTimer.current = null
      const pending = pendingWrite.current
      pendingWrite.current = null
      if (pending) sendPreference(scope, pending, keepalive)
    }
    // KAN-532 — closing or reloading the tab runs no React cleanup, so the
    // page being hidden sends the pending write now, with `keepalive` so it
    // outlives the page. `visibilitychange` to hidden is the signal a mobile
    // browser reliably gives before it discards a tab; `pagehide` covers the
    // close and the back/forward cache. While the page stays visible the
    // debounce is untouched.
    const onPageHide = () => flushPending(true)
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') flushPending(true)
    }
    window.addEventListener('pagehide', onPageHide)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.removeEventListener('pagehide', onPageHide)
      document.removeEventListener('visibilitychange', onVisibilityChange)
      // KAN-531 — runs on unmount and before a scope change, with this
      // scope: a write still waiting on its debounce goes out now, under the
      // scope it was made for — never 500 ms after its screen has gone.
      flushPending(false)
    }
  }, [scope])

  useEffect(() => {
    api
      .get<{ payload: Partial<T> }>(`/me/preferences/${scope}`)
      .then((res) => {
        if (Object.keys(res.payload).length > 0) {
          setValue((current) => ({ ...current, ...res.payload }))
        }
      })
      .catch(() => {
        // Never blocks the UI on a failed read — local mirror / defaults stand.
      })
    // Deliberately runs once per distinct scope, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope])

  const persist = useCallback(
    (next: T) => {
      writeLocalMirror(storageKey, next)
      if (writeTimer.current) clearTimeout(writeTimer.current)
      pendingWrite.current = next
      writeTimer.current = setTimeout(() => {
        writeTimer.current = null
        pendingWrite.current = null
        sendPreference(scope, next)
      }, WRITE_DEBOUNCE_MS)
    },
    [scope, storageKey]
  )

  const update = useCallback(
    (patch: Partial<T>) => {
      setValue((current) => {
        const next = { ...current, ...patch }
        persist(next)
        return next
      })
    },
    [persist]
  )

  return { value, update }
}

/** "Never block: a failed preference write is logged, never surfaces an error toast."
 *
 * KAN-532 — a keepalive send the browser refuses before it reaches the server
 * (a rejected fetch, not an API answer: the payload, or every keepalive body
 * in flight together, passed the browser's 64 KiB keepalive budget — the
 * server accepts up to 64 KiB per scope) is sent again as an ordinary
 * request, so hiding the page never loses a write the debounce would have
 * delivered. An `ApiError` is the server's answer and is not retried. */
function sendPreference(scope: string, payload: unknown, keepalive = false): void {
  api.put(`/me/preferences/${scope}`, payload, undefined, { keepalive }).catch((err) => {
    if (keepalive && !(err instanceof ApiError)) {
      sendPreference(scope, payload, false)
      return
    }
    console.error(`Failed to persist "${scope}" preferences:`, err instanceof ApiError ? err.message : err)
  })
}

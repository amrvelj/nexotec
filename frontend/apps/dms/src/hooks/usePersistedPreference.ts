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
      writeTimer.current = setTimeout(() => {
        api.put(`/me/preferences/${scope}`, next).catch((err) => {
          console.error(`Failed to persist "${scope}" preferences:`, err instanceof ApiError ? err.message : err)
        })
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

import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Density, UiLanguage } from '@nexotec/ui-kit'

const SCOPE = 'ui'
const STORAGE_KEY = 'dms.preferences.ui'
const WRITE_DEBOUNCE_MS = 500

interface UiPreferencesPayload {
  schemaVersion: number
  sidebarCollapsed: boolean
  uiLanguage: UiLanguage
  density: Density
}

const DEFAULTS: UiPreferencesPayload = {
  schemaVersion: 1,
  sidebarCollapsed: false,
  uiLanguage: 'de',
  density: 'default',
}

function readLocalMirror(): UiPreferencesPayload {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULTS
    return { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {
    return DEFAULTS
  }
}

/**
 * Backs § User-Level Preference Persistence's `ui` scope
 * (sidebarCollapsed, uiLanguage, density — "persisted per user globally,
 * not per grid — a user who wants dense wants dense everywhere", FR-UI-03)
 * via GET/PUT /v1/me/preferences/ui.
 *
 * "Optimistic local mirror... so the grid paints in the correct layout on
 * the very first frame... The server is the source of truth." — reads
 * localStorage synchronously on mount for an instant, correct-looking
 * first paint, then reconciles with the server's actual value once it
 * responds (server wins on conflict).
 *
 * "Write timing: Debounced 500ms after the last change" and "Never block:
 * a failed preference write is logged... never surfaces an error toast."
 */
export function useUiPreferences() {
  const [preferences, setPreferences] = useState<UiPreferencesPayload>(readLocalMirror)
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    api
      .get<{ payload: Partial<UiPreferencesPayload> }>(`/me/preferences/${SCOPE}`)
      .then((res) => {
        if (Object.keys(res.payload).length > 0) {
          setPreferences((current) => ({ ...current, ...res.payload }) as UiPreferencesPayload)
        }
      })
      .catch(() => {
        // Never blocks the UI on a failed read — local mirror / defaults stand.
      })
  }, [])

  const persist = useCallback((next: UiPreferencesPayload) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    if (writeTimer.current) clearTimeout(writeTimer.current)
    writeTimer.current = setTimeout(() => {
      api.put(`/me/preferences/${SCOPE}`, next).catch((err) => {
        console.error('Failed to persist UI preferences:', err instanceof ApiError ? err.message : err)
      })
    }, WRITE_DEBOUNCE_MS)
  }, [])

  const setSidebarCollapsed = useCallback(
    (sidebarCollapsed: boolean) => {
      setPreferences((current) => {
        const next = { ...current, sidebarCollapsed }
        persist(next)
        return next
      })
    },
    [persist]
  )

  const setUiLanguage = useCallback(
    (uiLanguage: UiLanguage) => {
      setPreferences((current) => {
        const next = { ...current, uiLanguage }
        persist(next)
        return next
      })
    },
    [persist]
  )

  const setDensity = useCallback(
    (density: Density) => {
      setPreferences((current) => {
        const next = { ...current, density }
        persist(next)
        return next
      })
    },
    [persist]
  )

  return {
    sidebarCollapsed: preferences.sidebarCollapsed,
    uiLanguage: preferences.uiLanguage,
    density: preferences.density,
    setSidebarCollapsed,
    setUiLanguage,
    setDensity,
  }
}

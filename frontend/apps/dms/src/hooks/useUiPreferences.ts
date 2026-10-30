import { usePersistedPreference } from './usePersistedPreference'
import { UI_PREFERENCES_STORAGE_KEY } from './persistedPreferenceStorage'
import type { Density, UiLanguage } from '@nexotec/ui-kit'

const SCOPE = 'ui'
const STORAGE_KEY = UI_PREFERENCES_STORAGE_KEY

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

/**
 * Backs § User-Level Preference Persistence's `ui` scope
 * (sidebarCollapsed, uiLanguage, density — "persisted per user globally,
 * not per grid — a user who wants dense wants dense everywhere", FR-UI-03)
 * via GET/PUT /v1/me/preferences/ui. WP-6c: the debounce/mirror/never-
 * block mechanics this hook pioneered now live in the generic
 * `usePersistedPreference`, which this and `useGridPreferences` both use —
 * this file just supplies the `ui` scope's own shape and setter names.
 */
export function useUiPreferences() {
  const { value, update } = usePersistedPreference<UiPreferencesPayload>(SCOPE, STORAGE_KEY, DEFAULTS)

  return {
    sidebarCollapsed: value.sidebarCollapsed,
    uiLanguage: value.uiLanguage,
    density: value.density,
    setSidebarCollapsed: (sidebarCollapsed: boolean) => update({ sidebarCollapsed }),
    setUiLanguage: (uiLanguage: UiLanguage) => update({ uiLanguage }),
    setDensity: (density: Density) => update({ density }),
  }
}

import { usePersistedPreference } from './usePersistedPreference'
import type { ColumnLayoutState, SortSpec } from '@nexotec/ui-kit'

export interface SavedViewSnapshot {
  columnLayout?: ColumnLayoutState
  sort?: SortSpec[]
}

export interface SavedView {
  id: string
  name: string
  isDefault?: boolean
  snapshot: SavedViewSnapshot
}

interface SavedViewsPayload {
  schemaVersion: number
  views: SavedView[]
}

const DEFAULTS: SavedViewsPayload = { schemaVersion: 1, views: [] }

/**
 * § Views and filters (ADR-058), the "Views" section — named snapshots a
 * user can switch between, distinct from `useGridPreferences`'s single
 * always-current working layout. Stored under its own `views:<gridKey>`
 * scope rather than nested inside the grid scope, so switching a view
 * doesn't have to round-trip the entire view LIST on every column resize.
 */
export function useSavedViews(gridKey: string) {
  const { value, update } = usePersistedPreference<SavedViewsPayload>(
    `views:${gridKey}`,
    `dms.preferences.views.${gridKey}`,
    DEFAULTS
  )

  const saveView = (name: string, snapshot: SavedViewSnapshot) => {
    const view: SavedView = { id: crypto.randomUUID(), name, snapshot }
    update({ views: [...value.views, view] })
  }

  const renameView = (id: string, name: string) => {
    update({ views: value.views.map((v) => (v.id === id ? { ...v, name } : v)) })
  }

  const deleteView = (id: string) => {
    update({ views: value.views.filter((v) => v.id !== id) })
  }

  const setDefaultView = (id: string | null) => {
    update({ views: value.views.map((v) => ({ ...v, isDefault: v.id === id })) })
  }

  return { views: value.views, saveView, renameView, deleteView, setDefaultView }
}

import { usePersistedPreference } from './usePersistedPreference'
import { markDefault, removeView, renameView as renameViewIn, upsertView, type SavedView, type SavedViewSnapshot } from '@nexotec/ui-kit'

export type { SavedView, SavedViewSnapshot }

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
 * The list-manipulation logic itself (`upsertView`/`removeView`/…) is
 * ui-kit's own pure, tested `savedView.ts` — this hook only adds
 * persistence on top of it.
 */
export function useSavedViews(gridKey: string) {
  const { value, update } = usePersistedPreference<SavedViewsPayload>(
    `views:${gridKey}`,
    `dms.preferences.views.${gridKey}`,
    DEFAULTS
  )

  const saveView = (name: string, snapshot: SavedViewSnapshot) => {
    const view: SavedView = { id: crypto.randomUUID(), name, snapshot }
    update({ views: upsertView(value.views, view) })
  }

  const renameView = (id: string, name: string) => {
    update({ views: renameViewIn(value.views, id, name) })
  }

  const deleteView = (id: string) => {
    update({ views: removeView(value.views, id) })
  }

  const setDefaultView = (id: string | null) => {
    update({ views: markDefault(value.views, id) })
  }

  return { views: value.views, saveView, renameView, deleteView, setDefaultView }
}

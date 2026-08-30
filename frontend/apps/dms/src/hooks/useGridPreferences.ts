import { usePersistedPreference } from './usePersistedPreference'
import type { ColumnLayoutState, SortSpec } from '@nexotec/ui-kit'

interface GridPreferencesPayload {
  schemaVersion: number
  /** `null` — no override yet, `DataGrid`/`ColumnConfigPanel` render the
   * module's own `columns` prop exactly as given (see `columnLayout`'s own
   * docstring in ui-kit: "omit for a grid with no user layout control at
   * all"). Reconciling a stored-but-stale layout against the module's
   * live column registry (a column removed/added/re-locked since it was
   * saved) is `resolveColumnLayout`'s job, run inside `DataGrid` itself —
   * this hook only ever stores the raw value it was given. */
  columnLayout: ColumnLayoutState | null
  sort: SortSpec[]
}

/**
 * § User-Level Preference Persistence, per-grid scope (`grid:<gridKey>`) —
 * column layout and sort, persisted per user per grid (U-01/U-09).
 * Deliberately NOT density: FR-UI-03 is explicit that density is "persisted
 * per user GLOBALLY, not per grid" — that's `useUiPreferences`'s `density`
 * field, reused as-is by every grid, never duplicated here. Chips and
 * custom filters join this payload in PR-7 once `FilterBuilder` gives them
 * a concrete shape to persist — extending this hook's payload then, not
 * inventing a placeholder shape now.
 */
export function useGridPreferences(gridKey: string, moduleDefaults: { sort: SortSpec[] }) {
  const defaults: GridPreferencesPayload = { schemaVersion: 1, columnLayout: null, sort: moduleDefaults.sort }
  const { value, update } = usePersistedPreference<GridPreferencesPayload>(
    `grid:${gridKey}`,
    `dms.preferences.grid.${gridKey}`,
    defaults
  )

  return {
    columnLayout: value.columnLayout,
    sort: value.sort,
    setColumnLayout: (columnLayout: ColumnLayoutState) => update({ columnLayout }),
    setSort: (sort: SortSpec[]) => update({ sort }),
  }
}

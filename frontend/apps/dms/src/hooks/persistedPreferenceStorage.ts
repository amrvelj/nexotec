/**
 * The pure half of `usePersistedPreference` — reading/writing the
 * localStorage optimistic mirror, framework-free so it's testable without
 * mounting a hook (no `renderHook`/testing-library dependency in this
 * workspace). Extracted from `useUiPreferences.ts`'s own original
 * `readLocalMirror`, which this generalizes to an arbitrary payload shape
 * and storage key instead of the one hardcoded `ui` scope.
 */
export function readLocalMirror<T>(storageKey: string, defaults: T): T {
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<T>
    return { ...defaults, ...parsed }
  } catch {
    return defaults
  }
}

export function writeLocalMirror<T>(storageKey: string, value: T): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(value))
  } catch {
    // A full or disabled localStorage never blocks the UI — the value
    // still reaches the server via the debounced PUT either way.
  }
}

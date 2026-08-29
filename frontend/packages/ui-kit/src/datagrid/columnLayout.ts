/**
 * § Columns (ADR-060): "every persisted field is available as a grid
 * column. A documented subset is visible by default. A field that is
 * stored and cannot be put on screen is a defect." This module is the
 * pure state layer underneath `ColumnConfigPanel` and `DataGrid`'s own
 * column rendering — deliberately free of React so it stays testable
 * without a DOM (this package has no jsdom/testing-library dependency),
 * and reusable as-is by PR-6's `useGridPreferences` once persistence is
 * wired in.
 */

export interface ColumnRegistryEntry {
  id: string;
  /** Plain-string display name for the panel's row — `header` on a
   * `GridColumnDef` can be a render function, which isn't usable as list
   * text. Falls back to `id` for a column a caller didn't bother naming
   * (visible in the panel as a slightly ugly identifier — a nudge to add
   * a real label, not a crash). */
  label: string;
  defaultVisible: boolean;
  /** Cannot be hidden — an identifying, primary, or action column.
   * Re-asserted visible by `resolveColumnLayout` even against a stale
   * saved layout that predates the column becoming locked. */
  locked?: boolean;
}

export interface ColumnLayoutState {
  /** Every known column id, in display order — visible and hidden alike,
   * so re-showing a hidden column restores it to its old position. */
  order: string[];
  hidden: string[];
  /** Column id -> pixel width override. Absent entries use the column's
   * own default sizing. */
  widths: Record<string, number>;
  /** Columns the USER has pinned left, on top of whatever a column's own
   * `GridColumnMeta.pinned` already declares at design time. */
  pinnedLeft: string[];
}

export interface ResolvedColumnLayout {
  /** Visible column ids, in effective order. */
  visibleOrder: string[];
  hiddenIds: Set<string>;
  widths: Record<string, number>;
  pinnedLeftIds: Set<string>;
}

export function defaultColumnLayout(registry: ColumnRegistryEntry[]): ColumnLayoutState {
  return {
    order: registry.map((c) => c.id),
    hidden: registry.filter((c) => !c.defaultVisible).map((c) => c.id),
    widths: {},
    pinnedLeft: [],
  };
}

/**
 * Reconciles a possibly-stale saved layout against the column registry a
 * screen ships TODAY — a column removed from the registry since the
 * layout was saved is dropped silently (nothing to show), a column added
 * since is appended at the end (never inserted mid-order, which would be
 * guessing where the user would have wanted it), and a locked column is
 * always forced visible regardless of what a stale `hidden` list says.
 */
export function resolveColumnLayout(registry: ColumnRegistryEntry[], layout: ColumnLayoutState): ResolvedColumnLayout {
  const knownIds = new Set(registry.map((c) => c.id));
  const lockedIds = new Set(registry.filter((c) => c.locked).map((c) => c.id));

  const orderedKnown = layout.order.filter((id) => knownIds.has(id));
  const missing = registry.map((c) => c.id).filter((id) => !orderedKnown.includes(id));
  const fullOrder = [...orderedKnown, ...missing];

  const hiddenIds = new Set(layout.hidden.filter((id) => knownIds.has(id) && !lockedIds.has(id)));
  const visibleOrder = fullOrder.filter((id) => !hiddenIds.has(id));

  return {
    visibleOrder,
    hiddenIds,
    widths: layout.widths,
    pinnedLeftIds: new Set(layout.pinnedLeft.filter((id) => knownIds.has(id))),
  };
}

export function toggleColumnVisibility(layout: ColumnLayoutState, id: string, locked: boolean): ColumnLayoutState {
  if (locked) return layout; // a locked column ignores a hide request outright
  const isHidden = layout.hidden.includes(id);
  return {
    ...layout,
    hidden: isHidden ? layout.hidden.filter((h) => h !== id) : [...layout.hidden, id],
  };
}

/** Moves `id` to sit immediately before `beforeId` (or to the end when
 * `beforeId` is null) — used by the panel's drag-reorder. */
export function reorderColumn(layout: ColumnLayoutState, id: string, beforeId: string | null): ColumnLayoutState {
  const withoutMoved = layout.order.filter((existing) => existing !== id);
  const insertAt = beforeId === null ? withoutMoved.length : withoutMoved.indexOf(beforeId);
  const targetIndex = insertAt === -1 ? withoutMoved.length : insertAt;
  return {
    ...layout,
    order: [...withoutMoved.slice(0, targetIndex), id, ...withoutMoved.slice(targetIndex)],
  };
}

export function resizeColumn(layout: ColumnLayoutState, id: string, width: number): ColumnLayoutState {
  return { ...layout, widths: { ...layout.widths, [id]: Math.max(60, Math.round(width)) } };
}

export function togglePinned(layout: ColumnLayoutState, id: string): ColumnLayoutState {
  const isPinned = layout.pinnedLeft.includes(id);
  return {
    ...layout,
    pinnedLeft: isPinned ? layout.pinnedLeft.filter((p) => p !== id) : [...layout.pinnedLeft, id],
  };
}

export function resetColumnLayout(registry: ColumnRegistryEntry[]): ColumnLayoutState {
  return defaultColumnLayout(registry);
}

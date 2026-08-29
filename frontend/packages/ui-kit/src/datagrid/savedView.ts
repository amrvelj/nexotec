import type { ColumnLayoutState } from "./columnLayout";
import type { FilterPredicate } from "./filterPredicate";
import type { SortSpec } from "./types";

/**
 * § Views and filters (ADR-058), the "Views" section. The type lives here
 * (ui-kit), not in the app-level `useSavedViews` hook that actually
 * persists it — `ViewsAndFilters` (a presentational component) needs the
 * shape to render, and a ui-kit component never imports from an app.
 */
export interface SavedViewSnapshot {
  columnLayout?: ColumnLayoutState;
  sort?: SortSpec[];
  filters?: FilterPredicate[];
}

export interface SavedView {
  id: string;
  name: string;
  isDefault?: boolean;
  snapshot: SavedViewSnapshot;
}

export function upsertView(views: SavedView[], view: SavedView): SavedView[] {
  const exists = views.some((v) => v.id === view.id);
  return exists ? views.map((v) => (v.id === view.id ? view : v)) : [...views, view];
}

export function removeView(views: SavedView[], id: string): SavedView[] {
  return views.filter((v) => v.id !== id);
}

export function renameView(views: SavedView[], id: string, name: string): SavedView[] {
  return views.map((v) => (v.id === id ? { ...v, name } : v));
}

/** At most one default at a time — passing `null` clears it entirely. */
export function markDefault(views: SavedView[], id: string | null): SavedView[] {
  return views.map((v) => ({ ...v, isDefault: v.id === id }));
}

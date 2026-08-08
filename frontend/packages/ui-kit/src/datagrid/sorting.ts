import type { SortSpec } from "./types";

const MAX_SORT_LEVELS = 3;

/**
 * § FR-UI-01 click cycle: "none → ascending → descending → none. The third
 * click returns to the grid's default sort" — modelled here as returning
 * to an empty sort array; the caller applies its own default when empty.
 * "Shift + click adds a secondary and tertiary sort, maximum 3 levels."
 *
 * A plain click always replaces the whole sort with just this field
 * (standard single-sort behaviour); a shift+click updates this field's
 * position in place if it's already part of the multi-sort, or appends it
 * if there's room under the 3-level cap.
 */
export function cycleSort(current: SortSpec[], field: string, additive: boolean): SortSpec[] {
  const existingIndex = current.findIndex((s) => s.field === field);

  if (!additive) {
    if (existingIndex === -1) return [{ field, direction: "asc" }];
    if (current[existingIndex].direction === "asc") return [{ field, direction: "desc" }];
    return [];
  }

  if (existingIndex === -1) {
    if (current.length >= MAX_SORT_LEVELS) return current;
    return [...current, { field, direction: "asc" }];
  }
  if (current[existingIndex].direction === "asc") {
    return current.map((s, i) => (i === existingIndex ? { field, direction: "desc" } : s));
  }
  return current.filter((_, i) => i !== existingIndex);
}

export function sortSpecToQueryParam(sort: SortSpec[]): string | undefined {
  if (sort.length === 0) return undefined;
  return sort.map((s) => `${s.field}:${s.direction}`).join(",");
}

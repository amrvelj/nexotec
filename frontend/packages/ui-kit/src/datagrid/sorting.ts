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

/** The inverse of `sortSpecToQueryParam` — `field:direction` pairs, comma
 * separated, as a grid keeps its sort in the URL (ADR-056). A part without
 * a field is dropped; any direction but `desc` reads as ascending; at most
 * the three levels a header click can build. */
export function sortSpecFromQueryParam(raw: string | null | undefined): SortSpec[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((part) => part.split(":"))
    .filter(([field]) => Boolean(field))
    .map(([field, direction]): SortSpec => ({ field, direction: direction === "desc" ? "desc" : "asc" }))
    .slice(0, MAX_SORT_LEVELS);
}

/** A row's value for a column a grid sorts in the browser. */
export type BrowserSortValue = string | number | boolean | null | undefined;

/** What `sortRowsInBrowser` reads of a column: the field its header sorts
 * by (`meta.sortField`) and the row's value for it (`meta.sortValue`). */
interface BrowserSortableColumn<T> {
  meta?: { sortField?: string; sortValue?: (row: T) => BrowserSortValue };
}

/**
 * KAN-516 (Anto, 2026-11-13, option C) — a short administration list whose
 * endpoint takes no sort (a library of a few dozen texts, a reference list,
 * a key search) sorts in the browser instead of on the server. `rows` is the
 * whole list the grid holds; the sort its header built (`cycleSort`) orders
 * it by each column's `meta.sortValue`, level by level. Text compares in the
 * reader's language (`Intl.Collator`; digits inside text in numeric order,
 * case and accents ignored), numbers and booleans by value; an empty value
 * (`null`, `undefined`, `""`) sorts last in either direction. A sort field no
 * column offers is ignored; an empty sort keeps the rows in the order they
 * came, and equal rows keep it too (the sort is stable). `rows` is never
 * mutated.
 */
export function sortRowsInBrowser<T>(
  rows: readonly T[],
  sort: readonly SortSpec[],
  columns: readonly BrowserSortableColumn<T>[],
  locale?: string
): T[] {
  const levels: { valueOf: (row: T) => BrowserSortValue; factor: number }[] = [];
  for (const spec of sort) {
    const valueOf = columns.find((c) => c.meta?.sortField === spec.field)?.meta?.sortValue;
    if (valueOf) levels.push({ valueOf, factor: spec.direction === "desc" ? -1 : 1 });
  }
  if (levels.length === 0) return [...rows];
  const collator = new Intl.Collator(locale, { numeric: true, sensitivity: "base" });
  const isEmpty = (value: BrowserSortValue) => value === null || value === undefined || value === "";
  return [...rows].sort((left, right) => {
    for (const level of levels) {
      const a = level.valueOf(left);
      const b = level.valueOf(right);
      if (isEmpty(a) || isEmpty(b)) {
        if (isEmpty(a) && isEmpty(b)) continue;
        return isEmpty(a) ? 1 : -1; // empty last, whatever the direction
      }
      const result =
        typeof a === "string" || typeof b === "string" ? collator.compare(String(a), String(b)) : Number(a) - Number(b);
      if (result !== 0) return result * level.factor;
    }
    return 0;
  });
}

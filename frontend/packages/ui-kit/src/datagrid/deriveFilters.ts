import type { FilterFieldDef } from "./filterPredicate";
import type { GridColumnDef } from "./types";

/**
 * § ADR-058 — "a view is a named set of filters, sorts and columns"; they
 * cannot be two hand-maintained lists. The Filter Builder's field list IS
 * the column registry: every column that declares `meta.filter` becomes
 * one filter field, in registry order, and a column without it is simply
 * not filterable. Adding a filterable column is one edit — the column def
 * — never a second edit to a parallel list.
 *
 * `header` on a `GridColumnDef` may be a render function, unusable as the
 * field's label; a column whose header is not a plain string falls back to
 * `meta.columnLabel` and then the column id. Pass columns whose `header`
 * is already the `t()`-resolved string (the common case) to get real
 * labels.
 */
export function deriveFilterFields<T>(columns: GridColumnDef<T>[]): FilterFieldDef[] {
  const fields: FilterFieldDef[] = [];
  for (const column of columns) {
    const filter = column.meta?.filter;
    if (!filter) continue;
    const id = String(column.id ?? ("accessorKey" in column ? column.accessorKey : ""));
    const label = typeof column.header === "string" ? column.header : (column.meta?.columnLabel ?? id);
    fields.push({
      id,
      label,
      type: filter.type,
      options: filter.options,
      param: filter.param ?? id,
      conditions: filter.conditions,
    });
  }
  return fields;
}

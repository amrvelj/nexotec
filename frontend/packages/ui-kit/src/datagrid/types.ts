import type { ColumnDef } from "@tanstack/react-table";
import type { ReactNode } from "react";
import type { FilterFieldOption, FilterFieldType } from "./filterPredicate";

export type SortDirection = "asc" | "desc";

export interface SortSpec {
  field: string;
  direction: SortDirection;
}

/** "Three densities... persisted per user globally (not per grid — a user
 * who wants dense wants dense everywhere)" — FR-UI-03. */
export type Density = "compact" | "default" | "comfortable";

export const ROW_HEIGHT: Record<Density, number> = {
  compact: 32,
  default: 40,
  comfortable: 56,
};

export interface GridColumnMeta<T> {
  /** API field name this column sorts by (must be in the entity's
   * server-side sort allow-list). Omit for a non-sortable column. */
  sortField?: string;
  pinned?: "left" | "right";
  mono?: boolean;
  align?: "left" | "right";
  /** Pixel width for an ordinary (non-pinned-synthetic) column — set by a
   * user's own resize drag via `ColumnLayoutState.widths`, applied by
   * `DataGrid` on top of whatever a column def declares here at design
   * time. Omit for the default flexible (`flex: 1 1 0`) sizing. */
  width?: number;
  /** Second line under the primary cell content at `comfortable` density,
   * inline with it at `default`, absent at `compact` (§ Composite cells). */
  secondary?: (row: T) => ReactNode;
  /** Plain-string display name for `ColumnConfigPanel`'s row (`header` can
   * be a render function, unusable as list text). Falls back to the
   * column's own `id` when omitted. */
  columnLabel?: string;
  /** § ADR-060 — every persisted field is a column; this is the
   * "documented visible subset" flag. Defaults to `true` so every column
   * def written before this existed keeps behaving exactly as it did. */
  defaultVisible?: boolean;
  /** An identifying, primary, or action column — cannot be hidden via
   * `ColumnConfigPanel`, and is re-asserted visible against a stale saved
   * layout (`resolveColumnLayout`). */
  locked?: boolean;
  /** § ADR-058 — "a view is a named set of filters, sorts and columns";
   * they cannot be two hand-maintained lists. A column that declares this
   * becomes a filter field, derived from the column registry by
   * `deriveFilterFields` — never a second, separate list that drifts the
   * moment a column is added. Omit for a column the backing list endpoint
   * cannot filter on. */
  filter?: {
    type: FilterFieldType;
    /** API query parameter this predicate maps to. Defaults to the column
     * id. The stored predicate's `fieldId` stays the column id regardless,
     * so a saved view / pasted URL survives a param rename. */
    param?: string;
    /** Required for `type: "select"` — every legal value, translated. */
    options?: FilterFieldOption[];
    /** Restrict the offered conditions to this subset of the type's full
     * set (`CONDITIONS_BY_TYPE`). Use it to stop offering a predicate the
     * API cannot honour, rather than accepting it and dropping it
     * silently. Omit to offer every condition for the type. */
    conditions?: string[];
  };
  /** Plain-text value of this column for a row, for CSV export / print —
   * `cell` returns a `ReactNode` (a badge, a link), unusable as file text.
   * A column with no `exportValue` contributes an empty cell to the
   * export. */
  exportValue?: (row: T) => string | number | null | undefined;
}

/**
 * A row's own link (`rowHref`) is a `position: absolute` sibling BEHIND
 * cell content (`.dg-row-link` in datagrid.css) — never a wrapping `<a>`,
 * which would make a real link rendered inside a cell invalid nested HTML.
 * A pinned cell already wins over it (pinned cells are `position: sticky`,
 * so they paint above the row link regardless). An UNPINNED cell's own
 * `cell` renderer that needs a real link/button to win over the row click
 * (§ The Data Grid: "a link inside a cell wins over the row click") must
 * add this class to that element — it opts the element into the same
 * "positioned" paint layer as the row link, and DOM order (the cell renders
 * after the link) puts it on top. Nothing renders it automatically, since
 * DataGrid doesn't control what a caller's `cell` renderer returns.
 */
export const DG_CELL_LINK_CLASS = "dg-cell-link";

export type GridColumnDef<T> = ColumnDef<T, unknown> & { meta?: GridColumnMeta<T> };

export interface EmptyStateConfig {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}

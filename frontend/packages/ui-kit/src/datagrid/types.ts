import type { ColumnDef } from "@tanstack/react-table";
import type { ReactNode } from "react";

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

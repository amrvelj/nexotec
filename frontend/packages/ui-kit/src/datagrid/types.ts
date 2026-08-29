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
  /** Second line under the primary cell content at `comfortable` density,
   * inline with it at `default`, absent at `compact` (§ Composite cells). */
  secondary?: (row: T) => ReactNode;
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

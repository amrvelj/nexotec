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
  /** Second line under the primary cell content (§ Composite cells) —
   * collapses to nothing in compact density. */
  secondary?: (row: T) => ReactNode;
}

export type GridColumnDef<T> = ColumnDef<T, unknown> & { meta?: GridColumnMeta<T> };

export interface EmptyStateConfig {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}

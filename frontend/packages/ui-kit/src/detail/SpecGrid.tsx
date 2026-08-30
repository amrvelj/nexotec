import type { ReactNode } from "react";
import { slate, spacing, typography } from "../tokens";

export interface SpecGridItem {
  label: string;
  value: ReactNode;
}

export interface SpecGridProps {
  items: SpecGridItem[];
  /**
   * 1–4 — the spec's own hard cap. There is deliberately no "responsive,
   * fewer columns on a narrow card" behaviour: a per-card width query
   * would wrap a long value (a VIN) one character per line on a narrow
   * card, which is worse than a fixed column count ever is. That also
   * means this belongs ONLY inside a full-width card — a half-width one
   * with 4 columns is exactly the layout this was built to avoid, and
   * nothing here can detect that mistake for you.
   */
  columns?: 1 | 2 | 3 | 4;
}

/**
 * § Detail Screens — the multi-column key/value spec grid. A plain CSS
 * grid, not a table: a table's own column-width algorithm would fight the
 * "every value gets equal, generous width" look this is meant to have.
 */
export function SpecGrid({ items, columns = 2 }: SpecGridProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        rowGap: spacing.md,
        columnGap: spacing.lg,
      }}
    >
      {items.map((item, index) => (
        <div key={index} style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: typography.label.size,
              fontWeight: typography.label.weight,
              color: slate[5],
              marginBottom: 2,
            }}
          >
            {item.label}
          </div>
          <div
            style={{
              fontSize: typography.body.size,
              color: slate[9],
              overflowWrap: "break-word",
            }}
          >
            {item.value}
          </div>
        </div>
      ))}
    </div>
  );
}

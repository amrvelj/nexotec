import type { ReactNode } from "react";
import { radius, semantic, shadow, slate, spacing, typography, white } from "../tokens";

export interface Stat {
  label: string;
  value: ReactNode;
  /** Renders the value in red — e.g. a trade-in deduction shown as a
   * negative figure (confirmed live on the contract detail screen). */
  negative?: boolean;
}

export interface StatRowProps {
  stats: Stat[];
}

/**
 * WP-8 PR-6 — the contract detail's confirmed 4-stat hero row
 * (Verkaufspreis / Eintauschfahrzeug / Zu bezahlen / Marge). Nothing
 * comparable existed in the library before this — DetailHeader/OverviewCard
 * are both shaped for identity and key-value pairs, not a small set of
 * headline figures across the top of a screen.
 */
export function StatRow({ stats }: StatRowProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${stats.length}, 1fr)`,
        gap: spacing.md,
      }}
    >
      {stats.map((stat, i) => (
        <div
          key={i}
          style={{
            borderRadius: radius.lg,
            boxShadow: shadow.sm,
            border: `1px solid ${slate[2]}`,
            backgroundColor: white,
            padding: spacing.md,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <span
            style={{
              fontSize: typography.microLabel.size,
              fontWeight: typography.microLabel.weight,
              letterSpacing: typography.microLabel.letterSpacing,
              textTransform: "uppercase" as const,
              color: typography.microLabel.color,
            }}
          >
            {stat.label}
          </span>
          <span style={{ fontSize: 20, fontWeight: 700, color: stat.negative ? semantic.destructive.text : slate[9] }}>
            {stat.value}
          </span>
        </div>
      ))}
    </div>
  );
}

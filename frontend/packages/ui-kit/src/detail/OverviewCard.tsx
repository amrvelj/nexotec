import type { ReactNode } from "react";
import { radius, shadow, slate, spacing, typography, white } from "../tokens";

export interface OverviewCardProps {
  title: string;
  /** WP-8 PR-2 — the container workspace's own status badge
   * (Erforderlich/Optional/Platzhalter, confirmed live), rendered beside
   * the title. Optional: most Overview-tab cards (Customer 360, Stock)
   * have no such concept and simply omit it. */
  badge?: ReactNode;
  children: ReactNode;
}

/**
 * § UI/UX Core Principles — Detail Screens, "Overview tab": "a titled
 * group of key–value rows". The two-column responsive grid these cards
 * sit in is the caller's layout (a plain CSS grid), since that's page
 * composition, not something this primitive needs to own.
 */
export function OverviewCard({ title, badge, children }: OverviewCardProps) {
  return (
    <div
      style={{
        borderRadius: radius.lg,
        boxShadow: shadow.sm,
        border: `1px solid ${slate[2]}`,
        backgroundColor: white,
        padding: spacing.lg,
        display: "flex",
        flexDirection: "column",
        gap: spacing.sm,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: spacing.sm }}>
        <div
          style={{
            fontSize: typography.microLabel.size,
            fontWeight: typography.microLabel.weight,
            letterSpacing: typography.microLabel.letterSpacing,
            textTransform: "uppercase" as const,
            color: typography.microLabel.color,
          }}
        >
          {title}
        </div>
        {badge}
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>{children}</div>
    </div>
  );
}

export interface KeyValueRowProps {
  label: string;
  children: ReactNode;
}

/** "micro-label section title, slate.5 key on the left, value right-
 * aligned in weight 500." Value rendering (including the italic slate.3
 * "Not set" empty state) is the child's responsibility — typically an
 * InlineEditField. */
export function KeyValueRow({ label, children }: KeyValueRowProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.md,
        padding: `${spacing.sm} 0`,
        borderBottom: `1px solid ${slate[1]}`,
        minHeight: 40,
      }}
    >
      <span style={{ fontSize: 13, color: slate[5], flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, textAlign: "right", fontWeight: 500, fontSize: 14, color: slate[9] }}>{children}</div>
    </div>
  );
}

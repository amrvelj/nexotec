import type { ReactNode } from "react";
import { radius, shadow, slate, spacing, typography, white, zIndex } from "../tokens";

export interface StickyActionFooterProps {
  /** e.g. "Es fehlt noch: Kunde, Fahrzeug" — confirmed live on the
   * generation workspace's own bottom bar. Omit (or pass null) once
   * nothing is missing. */
  missingLabel?: ReactNode;
  primaryAction: ReactNode;
}

/**
 * WP-8 PR-2 — the container generation workspace's always-visible bottom
 * bar: a missing-requirements summary on the left, one primary action on
 * the right ("Weiter zur Übersicht", confirmed live). Distinct from
 * `Wizard`'s own footer, which is step-indexed (Back/Next per step) — this
 * is a single persistent summary across a non-linear set of containers,
 * not a step sequence.
 *
 * KAN-305 — it carries its own stacking level (`zIndex.stickyBar`): the
 * content scrolling under it includes positioned controls (NumberInput's
 * spinners in the configurator), which would otherwise paint over the bar
 * and its primary action.
 */
export function StickyActionFooter({ missingLabel, primaryAction }: StickyActionFooterProps) {
  return (
    <div
      style={{
        position: "sticky",
        bottom: 0,
        zIndex: zIndex.stickyBar,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.md,
        padding: `${spacing.md} ${spacing.lg}`,
        borderRadius: radius.lg,
        border: `1px solid ${slate[2]}`,
        backgroundColor: white,
        boxShadow: shadow.md,
      }}
    >
      <span style={{ fontSize: typography.body.size, color: slate[5] }}>{missingLabel}</span>
      {primaryAction}
    </div>
  );
}

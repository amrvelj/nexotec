import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { semantic, spacing, typography } from "../tokens";

export interface EnvironmentNoticeProps {
  /** The translated sentence — the kit renders no text of its own. */
  children: ReactNode;
}

/**
 * § Application Shell (ADR-084) — a persistent, unobtrusive strip between
 * the top bar and the content region for a fact about THIS DEPLOYMENT that
 * a user would otherwise read as a defect: "this environment runs no
 * background worker: events are not delivered and nightly jobs do not run
 * here" (KAN-275, documenting ADR-081 option D). One line, warning tint,
 * no dismiss control: it stays true for as long as the deployment is
 * configured that way, and the tester who would close it is the tester it
 * is for. The kit knows nothing about environments or settings; the app
 * decides whether to pass one (AppShell's `notice` prop), and what it
 * says. AppShell measures it and publishes `--shell-notice-height` so the
 * overview grids keep filling the content region exactly.
 */
export function EnvironmentNotice({ children }: EnvironmentNoticeProps) {
  return (
    <div
      role="status"
      style={{
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        gap: spacing.xs,
        padding: `${spacing.xs} ${spacing.xl}`,
        fontSize: typography.meta.size,
        lineHeight: "16px",
        color: semantic.warning.ink,
        backgroundColor: semantic.warning.surface,
        borderBottom: `1px solid ${semantic.warning.border}`,
      }}
    >
      <AlertTriangle size={14} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

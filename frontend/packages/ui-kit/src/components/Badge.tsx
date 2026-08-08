import type { ReactNode } from "react";
import { purple, radius, slate, semantic, typography } from "../tokens";

export type BadgeTone = "purple" | "informational" | "success" | "warning" | "slate" | "destructive";

/**
 * Every tone's (text, surface, border) triple traces to an explicit token
 * usage note in tokens.ts — purple.1/purple.2 are documented as "badge
 * background"/"badge border", slate.1 as "neutral badge background",
 * purple.9 as "text on purple-tinted surfaces". The four semantic tones
 * use their tokens directly (that's their whole purpose).
 */
const TONE_COLORS: Record<BadgeTone, { text: string; surface: string; border: string }> = {
  purple: { text: purple[9], surface: purple[1], border: purple[2] },
  slate: { text: slate[6], surface: slate[1], border: slate[2] },
  informational: semantic.informational,
  success: semantic.success,
  warning: semantic.warning,
  destructive: semantic.destructive,
};

export interface BadgeProps {
  tone: BadgeTone;
  children: ReactNode;
  /** 14px icon element, e.g. `<User size={14} />` — for type badges. */
  icon?: ReactNode;
  /** 5px leading dot — for status badges. */
  dot?: boolean;
  strikethrough?: boolean;
}

/**
 * "Badges are pill-shaped (radius.full), 11px/600, with a 1px border and a
 * matching tinted surface. A status badge may carry a 5px leading dot; a
 * type badge carries a 14px icon." (§ Badges and Status)
 *
 * This is the low-level primitive — see CustomerTypeBadge,
 * LifecycleStatusBadge, LanguageBadge for the ready-to-use, spec-mapped
 * wrappers most screens should reach for instead of this directly.
 */
export function Badge({ tone, children, icon, dot, strikethrough }: BadgeProps) {
  const colors = TONE_COLORS[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "4px",
        padding: "2px 8px",
        borderRadius: radius.full,
        fontSize: typography.badge.size,
        fontWeight: typography.badge.weight,
        lineHeight: 1.4,
        whiteSpace: "nowrap",
        color: colors.text,
        backgroundColor: colors.surface,
        border: `1px solid ${colors.border}`,
        textDecoration: strikethrough ? "line-through" : undefined,
      }}
    >
      {dot && (
        <span
          aria-hidden="true"
          style={{
            width: "5px",
            height: "5px",
            borderRadius: "50%",
            backgroundColor: colors.text,
            flexShrink: 0,
          }}
        />
      )}
      {icon}
      {children}
    </span>
  );
}

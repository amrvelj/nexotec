import type { ReactNode } from "react";
import { X } from "lucide-react";
import { purple, radius, semantic, spacing, typography } from "../tokens";

export interface SelectionBarAction {
  label: string;
  icon?: ReactNode;
  onClick: () => void;
  destructive?: boolean;
}

export interface SelectionBarProps {
  count: number;
  onClear: () => void;
  actions: SelectionBarAction[];
  clearLabel?: string;
  /** Defaults to "N selected" — pass a translated pluralizer for anything
   * else. */
  countLabel?: (count: number) => string;
}

/**
 * § Action Bar — "selection replaces the chip row in place, never
 * stacked." This occupies the exact same layout slot as `FilterChips`
 * (same `marginBottom`, same position below the Action Bar) — the caller
 * renders EXACTLY ONE of the two, switching on whether anything is
 * selected, never both at once and never one above the other.
 */
export function SelectionBar({ count, onClear, actions, clearLabel = "Clear selection", countLabel }: SelectionBarProps) {
  if (count === 0) return null;
  const label = (countLabel ?? ((n: number) => `${n} selected`))(count);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: spacing.sm,
        padding: `${spacing.xs} ${spacing.md}`,
        marginBottom: spacing.md,
        borderRadius: radius.md,
        backgroundColor: purple[0],
        border: `1px solid ${purple[2]}`,
      }}
    >
      <span style={{ fontSize: typography.badge.size, fontWeight: 700, color: purple[9] }}>{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: spacing.sm, marginLeft: "auto" }}>
        {actions.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={action.onClick}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              border: "none",
              background: "none",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 600,
              color: action.destructive ? semantic.destructive.text : purple[7],
            }}
          >
            {action.icon}
            {action.label}
          </button>
        ))}
        <button
          type="button"
          onClick={onClear}
          aria-label={clearLabel}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 22,
            height: 22,
            border: "none",
            background: "none",
            borderRadius: radius.full,
            color: purple[7],
            cursor: "pointer",
          }}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

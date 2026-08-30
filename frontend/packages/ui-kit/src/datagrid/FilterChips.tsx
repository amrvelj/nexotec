import { X } from "lucide-react";
import { purple, radius, slate, spacing, typography } from "../tokens";

export interface FilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

export interface FilterChipsProps {
  chips: FilterChip[];
  onClearAll: () => void;
  /** Translated "Clear" label and remove-chip aria-label template —
   * default to English. */
  clearLabel?: string;
  removeLabel?: (chipLabel: string) => string;
}

/**
 * § Action Bar, Filter Chips zone — "( All 12'482 ) ( Individual 9'104 )
 * ( Business 3'378 ) ( Active ) ( × Clear )". Rendered below the Action
 * Bar, and only when filters exist — the caller decides that by not
 * rendering this component with an empty `chips` array.
 *
 * `SelectionBar` occupies this exact same slot when rows are selected —
 * "selection replaces the chip row in place, never stacked." A caller
 * with both selection and filters shows whichever one currently applies,
 * never both.
 */
export function FilterChips({
  chips,
  onClearAll,
  clearLabel = "Clear",
  removeLabel = (chipLabel) => `Remove filter: ${chipLabel}`,
}: FilterChipsProps) {
  if (chips.length === 0) return null;

  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: spacing.xs, marginBottom: spacing.md }}>
      {chips.map((chip) => (
        <span
          key={chip.key}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "4px",
            padding: "4px 6px 4px 10px",
            borderRadius: radius.full,
            fontSize: typography.badge.size,
            fontWeight: typography.badge.weight,
            color: purple[9],
            backgroundColor: purple[1],
            border: `1px solid ${purple[2]}`,
          }}
        >
          {chip.label}
          <button
            type="button"
            onClick={chip.onRemove}
            aria-label={removeLabel(chip.label)}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 16,
              height: 16,
              border: "none",
              background: "none",
              borderRadius: radius.full,
              color: purple[7],
              cursor: "pointer",
              padding: 0,
            }}
          >
            <X size={14} />
          </button>
        </span>
      ))}
      <button
        type="button"
        onClick={onClearAll}
        style={{
          border: "none",
          background: "none",
          color: slate[5],
          fontSize: typography.badge.size,
          fontWeight: typography.badge.weight,
          cursor: "pointer",
          padding: "4px 6px",
        }}
      >
        {clearLabel}
      </button>
    </div>
  );
}

import { useEffect, useRef, type ReactNode } from "react";
import { Rows3, RefreshCw, Search } from "lucide-react";
import { Tooltip } from "@mantine/core";
import { purple, radius, slate, spacing, white } from "../tokens";
import type { Density } from "./types";

export interface ActionBarProps {
  searchValue: string;
  onSearchChange: (value: string) => void;
  /** The screen's own search hint, translated — "Search customers…". */
  searchPlaceholder: string;
  density: Density;
  onDensityChange: (density: Density) => void;
  onRefresh: () => void;
  refreshing?: boolean;
  /** Zone 3 — a `<FilterButton>` instance, when the screen has structured
   * filters to back it. Rendered between search and the right cluster. */
  filterSlot?: ReactNode;
  /** § The Action Bar: "the right cluster is Density, Columns, Export,
   * Refresh — identical in all nine modules." `columnsSlot` (typically a
   * `<ColumnConfigPanel>`) and `exportSlot` are rendered in that fixed
   * position; `extraActions` is for anything genuinely screen-specific
   * that doesn't have a named slot yet, appended just before Refresh. */
  columnsSlot?: ReactNode;
  exportSlot?: ReactNode;
  extraActions?: ReactNode;
  labels: ActionBarLabels;
}

/** The bar's own strings (density cycle, refresh), translated by the
 * caller — the kit ships no English of its own (KAN-364), so `tsc` refuses
 * a caller that passes none. */
export interface ActionBarLabels {
  density: Record<Density, string>;
  /** The density button's tooltip, given the current density's name. */
  densityTooltip: (label: string) => string;
  densityAriaLabel: string;
  /** The refresh button's tooltip and accessible name. */
  refresh: string;
}

const DENSITY_ORDER: Density[] = ["compact", "default", "comfortable"];

/**
 * § The Action Bar. "Every overview screen in every module has exactly
 * this bar, in exactly this order." Saved views (zone 2) still aren't
 * implemented — no screen has a backing saved-views capability — so this
 * ships zones 1, 3, and 4; zone 3 (`filterSlot`) is only rendered when the
 * caller passes it, for screens without structured filters yet.
 */
export function ActionBar({
  searchValue,
  onSearchChange,
  searchPlaceholder,
  density,
  onDensityChange,
  onRefresh,
  refreshing,
  filterSlot,
  columnsSlot,
  exportSlot,
  extraActions,
  labels,
}: ActionBarProps) {
  const L = labels;
  const searchRef = useRef<HTMLInputElement>(null);

  // "Auto-focused on page load" (§ Action Bar, Zone 1).
  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  const cycleDensity = () => {
    const index = DENSITY_ORDER.indexOf(density);
    onDensityChange(DENSITY_ORDER[(index + 1) % DENSITY_ORDER.length]);
  };

  return (
    <div
      style={{
        height: 56,
        display: "flex",
        alignItems: "center",
        gap: spacing.sm,
        padding: `0 ${spacing.md}`,
        backgroundColor: white,
        border: `1px solid ${slate[2]}`,
        borderRadius: radius.lg,
        marginBottom: spacing.md,
      }}
    >
      <div style={{ flex: 1, position: "relative" }}>
        <Search size={16} color={slate[4]} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }} />
        <input
          ref={searchRef}
          value={searchValue}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onSearchChange("");
          }}
          placeholder={searchPlaceholder}
          style={{
            width: "100%",
            height: 36,
            padding: `0 ${spacing.sm} 0 34px`,
            border: `1px solid ${slate[3]}`,
            borderRadius: radius.md,
            fontSize: 14,
            outline: "none",
          }}
        />
      </div>

      {filterSlot}

      <div style={{ display: "flex", alignItems: "center", gap: spacing.xs }}>
        <Tooltip label={L.densityTooltip(L.density[density])}>
          <IconButton onClick={cycleDensity} aria-label={L.densityAriaLabel}>
            <Rows3 size={18} />
          </IconButton>
        </Tooltip>
        {columnsSlot}
        {exportSlot}
        {extraActions}
        <Tooltip label={L.refresh}>
          <IconButton onClick={onRefresh} aria-label={L.refresh}>
            <RefreshCw size={18} style={refreshing ? { opacity: 0.5 } : undefined} />
          </IconButton>
        </Tooltip>
      </div>
    </div>
  );
}

function IconButton({
  children,
  onClick,
  "aria-label": ariaLabel,
}: {
  children: ReactNode;
  onClick: () => void;
  "aria-label": string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        width: 32,
        height: 32,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        border: "none",
        background: "none",
        borderRadius: radius.sm,
        color: slate[6],
        cursor: "pointer",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = purple[0])}
      onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "transparent")}
    >
      {children}
    </button>
  );
}

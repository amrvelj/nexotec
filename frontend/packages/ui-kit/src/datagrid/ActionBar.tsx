import { useEffect, useRef, type ReactNode } from "react";
import { Rows3, RefreshCw, Search } from "lucide-react";
import { Tooltip } from "@mantine/core";
import { purple, radius, slate, spacing } from "../tokens";
import type { Density } from "./types";

export interface ActionBarProps {
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchPlaceholder?: string;
  density: Density;
  onDensityChange: (density: Density) => void;
  onRefresh: () => void;
  refreshing?: boolean;
  /** Extra icon buttons appended to the right cluster, e.g. a future
   * Columns/Export button — kept generic so per-screen buttons don't need
   * their own layout code. */
  extraActions?: ReactNode;
}

const DENSITY_ORDER: Density[] = ["compact", "default", "comfortable"];
const DENSITY_LABEL: Record<Density, string> = {
  compact: "Compact",
  default: "Default",
  comfortable: "Comfortable",
};

/**
 * § The Action Bar. "Every overview screen in every module has exactly
 * this bar, in exactly this order." Saved views (zone 2) and Filter
 * (zone 3) aren't implemented yet — no screen has structured filters or
 * saved views to back them, so this ships zones 1 and 4 only, in their
 * documented positions, rather than adding placeholder controls for
 * capabilities that don't exist.
 */
export function ActionBar({
  searchValue,
  onSearchChange,
  searchPlaceholder = "Search…",
  density,
  onDensityChange,
  onRefresh,
  refreshing,
  extraActions,
}: ActionBarProps) {
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
        backgroundColor: "#fff",
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

      <div style={{ display: "flex", alignItems: "center", gap: spacing.xs }}>
        <Tooltip label={`Density: ${DENSITY_LABEL[density]}`}>
          <IconButton onClick={cycleDensity} aria-label="Change row density">
            <Rows3 size={18} />
          </IconButton>
        </Tooltip>
        {extraActions}
        <Tooltip label="Refresh">
          <IconButton onClick={onRefresh} aria-label="Refresh">
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

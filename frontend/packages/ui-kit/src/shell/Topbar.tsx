import { slate, spacing } from "../tokens";
import { GlobalSearch, type GlobalSearchProps } from "./GlobalSearch";

export interface TopbarProps {
  /** Current page's breadcrumb ("Group / Entity / Record") — AppShell
   * supplies this from whatever the active page last set via
   * useSetBreadcrumb. */
  breadcrumb: string[];
  /** Omit only where no cross-entity search exists yet — every real module
   * wires this (§ FR-UI-08). */
  search?: GlobalSearchProps;
}

/**
 * § Topbar. Fixed 60px: breadcrumb left, global search centred, nothing
 * else — account chrome (language, sign out) lives in the sidebar's
 * account cluster, the only place it appears in the product (revised
 * 2026-08-16).
 */
export function Topbar({ breadcrumb, search }: TopbarProps) {
  return (
    <header
      style={{
        height: 60,
        flexShrink: 0,
        position: "sticky",
        top: 0,
        zIndex: 10,
        display: "grid",
        // Equal outer columns keep the centre column (the search box)
        // visually centred on the bar regardless of breadcrumb length,
        // rather than centred on whatever space the breadcrumb leaves.
        gridTemplateColumns: "1fr auto 1fr",
        alignItems: "center",
        gap: spacing.lg,
        padding: `0 ${spacing.xl}`,
        backgroundColor: "rgba(255,255,255,0.85)",
        backdropFilter: "blur(10px)",
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <Breadcrumb segments={breadcrumb} />
      </div>
      <div style={{ width: 560, maxWidth: "100%" }}>{search && <GlobalSearch {...search} />}</div>
      <div />
    </header>
  );
}

function Breadcrumb({ segments }: { segments: string[] }) {
  if (segments.length === 0) return <div />;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.xs, fontSize: 13 }}>
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        return (
          <span key={`${segment}-${index}`} style={{ display: "flex", alignItems: "center", gap: spacing.xs }}>
            {index > 0 && <span style={{ color: slate[3] }}>/</span>}
            <span style={{ color: isLast ? slate[9] : slate[5], fontWeight: isLast ? 700 : 400 }}>{segment}</span>
          </span>
        );
      })}
    </div>
  );
}

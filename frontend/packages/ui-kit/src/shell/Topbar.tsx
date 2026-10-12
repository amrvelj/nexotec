import { slate, spacing, zIndex } from "../tokens";
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
        zIndex: zIndex.stickyBar,
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

/**
 * KAN-301 — the breadcrumb never wraps. At 1024-1280 px the left grid column
 * is narrow (the centred search takes 560 px), and the record name wrapped
 * onto two lines and slid under the search. Each segment now stays on one
 * line and, when the bar is too narrow, is cut with an ellipsis, its full
 * text in `title`. The leading segments give way first; the record name
 * (the last segment, the one the user is on) shrinks only once they are
 * down to their minimum.
 */
function Breadcrumb({ segments }: { segments: string[] }) {
  if (segments.length === 0) return <div />;
  return (
    <div
      data-breadcrumb
      style={{ display: "flex", alignItems: "center", gap: spacing.xs, fontSize: 13, minWidth: 0, flexWrap: "nowrap", overflow: "hidden" }}
    >
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        return (
          <span
            key={`${segment}-${index}`}
            data-breadcrumb-segment
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.xs,
              minWidth: isLast ? "3em" : "2em",
              flex: isLast ? "0 1 auto" : "0 1000 auto",
            }}
          >
            {index > 0 && <span style={{ color: slate[3], flexShrink: 0 }}>/</span>}
            <span
              title={segment}
              style={{
                color: isLast ? slate[9] : slate[5],
                fontWeight: isLast ? 700 : 400,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                minWidth: 0,
              }}
            >
              {segment}
            </span>
          </span>
        );
      })}
    </div>
  );
}

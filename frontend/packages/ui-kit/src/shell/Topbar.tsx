import { slate, spacing } from "../tokens";

export interface TopbarProps {
  /** Current page's breadcrumb ("Group / Entity / Record") — AppShell
   * supplies this from whatever the active page last set via
   * useSetBreadcrumb. */
  breadcrumb: string[];
}

/**
 * § Topbar. Fixed 60px, breadcrumb only. "The topbar carries the breadcrumb
 * and global search and nothing else" — account chrome (language, sign out)
 * lives in the sidebar's account cluster, the only place it appears in the
 * product (revised 2026-08-16). Global search itself isn't built yet.
 */
export function Topbar({ breadcrumb }: TopbarProps) {
  return (
    <header
      style={{
        height: 60,
        flexShrink: 0,
        position: "sticky",
        top: 0,
        zIndex: 10,
        display: "flex",
        alignItems: "center",
        padding: `0 ${spacing.xl}`,
        backgroundColor: "rgba(255,255,255,0.85)",
        backdropFilter: "blur(10px)",
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      <Breadcrumb segments={breadcrumb} />
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

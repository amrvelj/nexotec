import { useEffect, useState, type ReactNode } from "react";
import { slate, spacing } from "../tokens";
import { BreadcrumbProvider } from "./BreadcrumbContext";
import { Sidebar, type SidebarProps } from "./Sidebar";
import { Topbar, type TopbarProps } from "./Topbar";

const AUTO_COLLAPSE_BREAKPOINT = 1280;

export interface AppShellProps {
  sidebar: Omit<SidebarProps, "collapsed" | "onToggleCollapsed">;
  topbar: Omit<TopbarProps, "breadcrumb">;
  /** Persisted (not runtime-forced) collapse state — e.g. from
   * user_preference scope "ui". AppShell layers the <1280px auto-collapse
   * on top of this without writing back to it (§ Sidebar: "Auto-collapse
   * below 1280px viewport width. This is a runtime override, not a
   * preference write — restoring width restores the user's chosen state"). */
  collapsed: boolean;
  onToggleCollapsed: () => void;
  children: ReactNode;
}

/**
 * The three-part shell (§ Application Shell): sidebar + topbar + content.
 * "The shell never scrolls; only the content region does."
 */
export function AppShell({ sidebar, topbar, collapsed, onToggleCollapsed, children }: AppShellProps) {
  const [breadcrumb, setBreadcrumb] = useState<string[]>([]);
  const [narrowViewport, setNarrowViewport] = useState(
    () => typeof window !== "undefined" && window.innerWidth < AUTO_COLLAPSE_BREAKPOINT
  );

  useEffect(() => {
    const handleResize = () => setNarrowViewport(window.innerWidth < AUTO_COLLAPSE_BREAKPOINT);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    const handleKeydown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const isTyping = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable;
      if (event.key === "[" && !isTyping) {
        onToggleCollapsed();
      }
    };
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [onToggleCollapsed]);

  return (
    <BreadcrumbProvider value={setBreadcrumb}>
      <div style={{ display: "flex", height: "100vh", backgroundColor: slate[0] }}>
        <Sidebar {...sidebar} collapsed={collapsed || narrowViewport} onToggleCollapsed={onToggleCollapsed} />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <Topbar {...topbar} breadcrumb={breadcrumb} />
          <main
            style={{
              flex: 1,
              overflowY: "auto",
              padding: `${spacing.xl} ${spacing.xl} 60px`,
            }}
          >
            {children}
          </main>
        </div>
      </div>
    </BreadcrumbProvider>
  );
}

import { useEffect, useRef, useState, type ReactNode } from "react";
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
  /** A persistent strip between the top bar and the content region — an
   * `EnvironmentNotice` saying something about this deployment the user
   * would otherwise mistake for a defect (KAN-275: staging runs no outbox
   * worker). Outside the scrolling `<main>`, so it stays in view; omitted
   * on every deployment that has nothing to say. */
  notice?: ReactNode;
  children: ReactNode;
}

/**
 * The three-part shell (§ Application Shell): sidebar + topbar + content,
 * plus an optional `notice` strip between topbar and content (ADR-084).
 * "The shell never scrolls; only the content region does."
 *
 * The notice's measured height is published on the content column as the
 * CSS custom property `--shell-notice-height` (0px when there is none), so
 * anything that sizes itself against the viewport minus the topbar —
 * OverviewShellRegion's sticky region — can subtract it and keep filling
 * <main> exactly, with no residual scroll.
 */
export function AppShell({ sidebar, topbar, collapsed, onToggleCollapsed, notice, children }: AppShellProps) {
  const [breadcrumb, setBreadcrumb] = useState<string[]>([]);
  const noticeRef = useRef<HTMLDivElement>(null);
  const [noticeHeight, setNoticeHeight] = useState(0);
  const [narrowViewport, setNarrowViewport] = useState(
    () => typeof window !== "undefined" && window.innerWidth < AUTO_COLLAPSE_BREAKPOINT
  );

  useEffect(() => {
    const handleResize = () => setNarrowViewport(window.innerWidth < AUTO_COLLAPSE_BREAKPOINT);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    const element = noticeRef.current;
    if (!element) {
      setNoticeHeight(0);
      return;
    }
    const measure = () => setNoticeHeight(element.offsetHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [notice]);

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
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
            ["--shell-notice-height" as string]: `${noticeHeight}px`,
          }}
        >
          <Topbar {...topbar} breadcrumb={breadcrumb} />
          {notice && (
            <div ref={noticeRef} style={{ flexShrink: 0 }}>
              {notice}
            </div>
          )}
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

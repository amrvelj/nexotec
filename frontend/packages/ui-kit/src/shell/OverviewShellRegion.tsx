import type { ReactNode } from "react";
import { layout } from "../tokens";

export interface OverviewShellRegionProps {
  /** The page title, subtitle, whatever else sits above the action bar.
   * Scrolls away with the page — this is the part of "the one layout
   * problem in the product that CSS makes genuinely hard" that's allowed
   * to move. */
  header: ReactNode;
  /** Search, views-and-filters, density/columns/export/refresh. Docks at
   * the top of the sticky region once the header has scrolled past it. */
  actionBar: ReactNode;
  /** The grid (or equivalent). Owns both scroll axes inside the region;
   * its own header row stays visible while its body scrolls under it. */
  children: ReactNode;
}

/**
 * § Scroll ownership. "Everything from the action bar down is one sticky
 * region, `100vh − topbar`, and the grid owns both scroll axes inside it."
 * AppShell's own `<main>` is the scrolling ancestor (`overflow-y: auto`) —
 * this region is a `position: sticky` child of it, so it docks at the top
 * of that scrollport once `header` has scrolled past, and its own fixed
 * height turns the grid's body into the only thing still scrolling once
 * docked. Every overview screen (five grids, ADR-066 included) renders
 * this once instead of five approximations of the same CSS.
 */
export function OverviewShellRegion({ header, actionBar, children }: OverviewShellRegionProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      {header}
      <div
        style={{
          position: "sticky",
          top: 0,
          display: "flex",
          flexDirection: "column",
          // AppShell's <main> already excludes the topbar (a flex sibling,
          // not an ancestor) — this only has to give back <main>'s own
          // padding so the sticky region fills exactly the remaining
          // viewport once docked, with no residual scroll at the bottom.
          height: `calc(100vh - ${layout.topbarHeight} - ${layout.contentPaddingTop} - ${layout.contentPaddingBottom})`,
          minHeight: 0,
        }}
      >
        <div style={{ flexShrink: 0 }}>{actionBar}</div>
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>{children}</div>
      </div>
    </div>
  );
}

import { describe, expect, it } from "vitest";
import { layout } from "../tokens";
import { OVERVIEW_REGION_HEIGHT } from "./OverviewShellRegion";

// ADR-084 — the docked region is exactly <main>'s content box: the viewport
// minus the topbar, minus the optional notice strip AppShell publishes as
// --shell-notice-height (0px without one), minus <main>'s own padding.
// Without the subtraction every overview grid overshoots by the strip's
// height and its action bar is clipped at full scroll.
describe("OverviewShellRegion height", () => {
  it("subtracts the notice strip AppShell publishes, defaulting to 0px", () => {
    expect(OVERVIEW_REGION_HEIGHT).toContain("var(--shell-notice-height, 0px)");
  });

  it("still gives back the topbar and <main>'s padding", () => {
    expect(OVERVIEW_REGION_HEIGHT).toBe(
      `calc(100vh - ${layout.topbarHeight} - var(--shell-notice-height, 0px) - ${layout.contentPaddingTop} - ${layout.contentPaddingBottom})`
    );
  });
});

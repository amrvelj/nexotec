import type { ComponentType } from "react";

export interface NavItemConfig {
  label: string;
  href: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  /** "soon" renders at 42% opacity with a SOON chip and isn't clickable —
   * § Sidebar: "Modules not yet built... stay visible deliberately, the
   * roadmap is part of the product story." */
  status?: "active" | "soon";
}

export interface NavGroupConfig {
  /** Micro-label group heading, e.g. "MASTER DATA" — uppercased by the
   * component, pass the plain label. Omit for a single top-level entry
   * that sits above the labelled groups with no heading of its own (the
   * Dashboard nav item is the only current case) — the collapsed-sidebar
   * divider line is skipped for it too, since there's nothing above it to
   * divide from. */
  label?: string;
  items: NavItemConfig[];
}

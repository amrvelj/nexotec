import type { ComponentType } from "react";

export interface NavItemConfig {
  label: string;
  href: string;
  icon: ComponentType<{ size?: number; strokeWidth?: number }>;
  /** "soon" renders at 42% opacity with a SOON chip and isn't clickable —
   * § Sidebar: "Modules not yet built... stay visible deliberately, the
   * roadmap is part of the product story." */
  status?: "active" | "soon";
  /** A compact state marker beside the label (KAN-223: the status page's
   * indicator — a green dot, or a warning/destructive dot with a count). The
   * app decides who sees it; the label is its accessible name (no kit
   * string). Shown on the icon when the sidebar is collapsed. */
  indicator?: NavItemIndicator;
}

export interface NavItemIndicator {
  tone: "success" | "warning" | "destructive";
  /** Shown beside the dot when above zero. */
  count?: number;
  /** The accessible name and tooltip, e.g. "Status: 2 items need attention". */
  label: string;
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

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
   * component, pass the plain label. */
  label: string;
  items: NavItemConfig[];
}

/**
 * Design tokens (UI/UX Core Principles § Design Tokens). "No component may
 * hardcode a colour, radius, spacing or shadow" — every visual value used
 * across Nexotec apps should trace back to a token exported here.
 *
 * purple[8] and slate[8] are not given explicit hex values in the source
 * doc (only 0/1/2/3/4/5/6/7/9 are specified for each) — Mantine requires a
 * full 10-shade tuple, so these two are interpolated between their
 * documented neighbours rather than left undefined. If the design system
 * later specifies them explicitly, update here.
 */

export const purple = [
  "#F5F3FF", // 0 — active nav background, row hover, selected-row tint, focus glow surface
  "#EDE9FE", // 1 — badge background, tab counter background
  "#DDD6FE", // 2 — badge border, text selection
  "#C4B5FD", // 3 — hover border on interactive cards
  "#A78BFA", // 4 — gradient stop, timeline markers
  "#8B5CF6", // 5 — focus ring border, gradient stop
  "#7C3AED", // 6 — primary: primary buttons, active tab underline, active chip, checked checkbox
  "#6D28D9", // 7 — primary button hover, active nav text
  "#5B21B6", // 8 — interpolated (not specified in source doc)
  "#4C1D95", // 9 — text on purple-tinted surfaces
] as const;

export const slate = [
  "#F8FAFC", // 0 — app background
  "#F1F5F9", // 1 — hover on ghost buttons, neutral badge background, row dividers on dense grids
  "#E2E8F0", // 2 — all borders and dividers
  "#CBD5E1", // 3 — input borders, placeholder-adjacent text
  "#94A3B8", // 4 — placeholders, disabled text, secondary icons
  "#64748B", // 5 — secondary text, column headers, metadata
  "#475569", // 6 — body text on secondary surfaces, nav labels
  "#334155", // 7 — form labels, secondary button text
  "#1E293B", // 8 — interpolated (not specified in source doc)
  "#0F172A", // 9 — primary text, headings
] as const;

/** Not part of the 0-9 scale — table header background, modal footer. */
export const slate25 = "#FCFCFD";

/**
 * "Colour carries meaning and nothing else. These four are the complete
 * semantic vocabulary." Only text/surface/border are specified per
 * semantic (not a full 10-shade scale) — deliberately not forced into
 * Mantine's colors.* tuple system, which would mean inventing seven more
 * shades per colour that the source doc never gave.
 */
export const semantic = {
  success: { text: "#059669", surface: "#ECFDF5", border: "#A7F3D0" },
  warning: { text: "#D97706", surface: "#FFFBEB", border: "#FDE68A" },
  destructive: { text: "#DC2626", surface: "#FEF2F2", border: "#FECACA" },
  informational: { text: "#2563EB", surface: "#EFF6FF", border: "#BFDBFE" },
} as const;

export const spacing = {
  xs: "4px", // icon-to-label gap inside a badge
  sm: "8px", // gap between buttons in a group
  md: "14px", // table cell horizontal padding, gap between action-bar clusters
  lg: "18px", // card padding, gap between cards
  xl: "24px", // content area padding
} as const;

export const radius = {
  sm: "7px", // nav items, small buttons, chips-with-square-ends
  md: "8px", // buttons, inputs, selects
  lg: "10px", // cards, panels, tables
  xl: "14px", // modals
  full: "9999px", // badges, filter chips, avatars
} as const;

export const shadow = {
  sm: "0 1px 2px rgba(15,23,42,.05)", // cards, resting elevation — the default
  md: "0 4px 12px rgba(15,23,42,.08)", // dropdowns, popovers, the row-action menu
  lg: "0 20px 50px rgba(76,29,149,.20)", // modals only
} as const;

export const focusRing = {
  glow: "0 0 0 3.5px #EDE9FE",
  border: "1px solid #8B5CF6",
} as const;

/** Inter, with the system stack as fallback — "never more than one family." */
export const fontFamily =
  'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

export const fontFamilyMono = "ui-monospace, SF Mono, Menlo, Consolas, monospace";

/** Typography roles (§ Typography). Sizes in px, weight, letter-spacing. */
export const typography = {
  pageTitle: { size: "22px", weight: 700, letterSpacing: "-0.4px" },
  sectionTitle: { size: "16px", weight: 600, letterSpacing: "-0.2px" },
  body: { size: "14px", weight: 400 },
  bodyStrong: { size: "14px", weight: 600 },
  secondary: { size: "13px", weight: 400, color: slate[5] },
  label: { size: "12.5px", weight: 600, color: slate[7] },
  meta: { size: "12px", weight: 400, color: slate[5] },
  microLabel: { size: "11px", weight: 700, letterSpacing: "0.7px", transform: "uppercase", color: slate[4] },
  badge: { size: "11px", weight: 600 },
  mono: { size: "12.5px", family: fontFamilyMono },
} as const;

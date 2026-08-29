/**
 * Design tokens (UI/UX Core Principles § Design Tokens). "No component may
 * hardcode a colour, radius, spacing or shadow" — every visual value used
 * across Nexotec apps should trace back to a token exported here.
 *
 * WP-6c: these values are now `var(--token)` strings backed by the real CSS
 * custom properties in `tokens.css` (imported once, globally, before any
 * Nexotec app renders), not baked-in hex/px literals — the reason being
 * exactly what makes a future dark mode (U-08) a `:root` override instead
 * of a rewrite: a CSS variable can be re-pointed at paint time, a JS string
 * captured at import time cannot. Every existing call site (`purple[6]`,
 * `slate[2]`, …) is unchanged — `style.background = "var(--purple-6)"` is a
 * perfectly ordinary CSS value, so nothing downstream had to change.
 *
 * purple[8] and slate[8] are the one exception: the source token set
 * (Notion's Design Tokens section, and the prototype's own tokens.css)
 * never defines them, so there is no `--purple-8`/`--slate-8` custom
 * property to point at. Mantine's `MantineColorsTuple` still needs a full
 * 10-shade array, so these two stay plain interpolated hex literals —
 * `tests/no-hardcoded-colour.test.ts` exempts this file by design, not by
 * accident, for exactly this reason.
 */

export const purple = [
  "var(--purple-0)", // 0 — active nav background, row hover, selected-row tint, focus glow surface
  "var(--purple-1)", // 1 — badge background, tab counter background
  "var(--purple-2)", // 2 — badge border, text selection
  "var(--purple-3)", // 3 — hover border on interactive cards
  "var(--purple-4)", // 4 — gradient stop, timeline markers
  "var(--purple-5)", // 5 — focus ring border, gradient stop
  "var(--purple-6)", // 6 — primary: primary buttons, active tab underline, active chip, checked checkbox
  "var(--purple-7)", // 7 — primary button hover, active nav text
  "#5B21B6", // 8 — interpolated (not specified in source doc — no CSS variable backs it)
  "var(--purple-9)", // 9 — text on purple-tinted surfaces
] as const;

export const slate = [
  "var(--slate-0)", // 0 — app background
  "var(--slate-1)", // 1 — hover on ghost buttons, neutral badge background, row dividers on dense grids
  "var(--slate-2)", // 2 — all borders and dividers
  "var(--slate-3)", // 3 — input borders, placeholder-adjacent text
  "var(--slate-4)", // 4 — placeholders, disabled text, secondary icons
  "var(--slate-5)", // 5 — secondary text, column headers, metadata
  "var(--slate-6)", // 6 — body text on secondary surfaces, nav labels
  "var(--slate-7)", // 7 — form labels, secondary button text
  "#1E293B", // 8 — interpolated (not specified in source doc — no CSS variable backs it)
  "var(--slate-9)", // 9 — primary text, headings
] as const;

/** Not part of the 0-9 scale — table header background, modal footer. */
export const slate25 = "var(--slate-25)";

/**
 * Not part of either scale — the one true white, for text/icons sitting on
 * a solid purple or semantic surface (a button's own label, a filled badge)
 * and for card/panel backgrounds that must stay white even against a dark
 * mode `--slate-0` app background later. Every literal `"#fff"` in the repo
 * predates this export; `tests/no-hardcoded-colour.test.ts` is what forced
 * finding and replacing every one of them when this token landed.
 */
export const white = "var(--white)";

/**
 * "Colour carries meaning and nothing else. These four are the complete
 * semantic vocabulary." Only text/surface/border are specified per
 * semantic (not a full 10-shade scale) — deliberately not forced into
 * Mantine's colors.* tuple system, which would mean inventing seven more
 * shades per colour that the source doc never gave.
 *
 * `ink` (WP-6c, from the prototype's own tokens.css — not in the Notion
 * page's prose) is the darkened member of the triplet, for text ON its own
 * tinted surface: `text` passes AA on white but not on `surface`, `ink`
 * does. `inkInverted` exists only for success/destructive, matching the
 * prototype's own two dark-surface cases (Stock's Wagenbuch, not built by
 * this package) — never guessed for warning/informational, which don't
 * have one in the source file.
 */
export const semantic = {
  success: { text: "var(--ok-fg)", surface: "var(--ok-bg)", border: "var(--ok-bd)", ink: "var(--ok-ink)", inkInverted: "var(--ok-ink-inv)" },
  warning: { text: "var(--warn-fg)", surface: "var(--warn-bg)", border: "var(--warn-bd)", ink: "var(--warn-ink)" },
  destructive: { text: "var(--danger-fg)", surface: "var(--danger-bg)", border: "var(--danger-bd)", ink: "var(--danger-ink)", inkInverted: "var(--danger-ink-inv)" },
  informational: { text: "var(--info-fg)", surface: "var(--info-bg)", border: "var(--info-bd)", ink: "var(--info-ink)" },
} as const;

/** The skeleton-loading shimmer tint — not part of the slate scale or the
 * semantic vocabulary, its own token in the source file. */
export const shimmer = "var(--shimmer)";

export const spacing = {
  xs: "var(--sp-xs)", // icon-to-label gap inside a badge
  sm: "var(--sp-sm)", // gap between buttons in a group
  md: "var(--sp-md)", // table cell horizontal padding, gap between action-bar clusters
  lg: "var(--sp-lg)", // card padding, gap between cards
  xl: "var(--sp-xl)", // content area padding
} as const;

export const radius = {
  sm: "var(--r-sm)", // nav items, small buttons, chips-with-square-ends
  md: "var(--r-md)", // buttons, inputs, selects
  lg: "var(--r-lg)", // cards, panels, tables
  xl: "var(--r-xl)", // modals
  full: "var(--r-full)", // badges, filter chips, avatars
} as const;

export const shadow = {
  sm: "var(--sh-sm)", // cards, resting elevation — the default
  md: "var(--sh-md)", // dropdowns, popovers, the row-action menu
  lg: "var(--sh-lg)", // modals only
} as const;

export const focusRing = {
  glow: "var(--focus-ring)",
  border: `1px solid var(--focus-bd)`,
} as const;

/** Layout dimensions (§ Layout dimensions) — the shell's own constants. */
export const layout = {
  sidebarWidth: "var(--sidebar-w)",
  sidebarWidthCollapsed: "var(--sidebar-w-collapsed)",
  topbarHeight: "var(--topbar-h)",
  actionBarHeight: "var(--actionbar-h)",
  contentPaddingX: "var(--content-pad-x)",
  contentPaddingTop: "var(--content-pad-top)",
  contentPaddingBottom: "var(--content-pad-bottom)",
  /** Forms and detail screens only — grids get no cap (§ Layout dimensions:
   * "Capping a grid at 1280px throws away columns on a 27" counter
   * monitor, which is exactly the wrong trade for a density-first tool"). */
  formMaxWidth: "var(--form-max-w)",
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

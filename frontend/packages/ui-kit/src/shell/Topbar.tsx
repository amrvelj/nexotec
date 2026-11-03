import type { ComponentType, CSSProperties, ReactNode } from "react";
import { slate, spacing, zIndex } from "../tokens";
import type { BreadcrumbSegment } from "./BreadcrumbContext";
import { GlobalSearch, type GlobalSearchProps } from "./GlobalSearch";

type LinkLike = ComponentType<{
  to: string;
  children?: ReactNode;
  style?: CSSProperties;
  className?: string;
  title?: string;
  "aria-label"?: string;
}>;

const DefaultLink: LinkLike = ({ to, children, style, className, title, "aria-label": ariaLabel }) => (
  <a href={to} style={style} className={className} title={title} aria-label={ariaLabel}>
    {children}
  </a>
);

/** The breadcrumb's root. With an `icon`, it is drawn as that icon alone —
 * the label is its accessible name and tooltip — so the page's own segments
 * keep their room in the bar (KAN-301's narrow column). */
export interface BreadcrumbRoot {
  label: string;
  href: string;
  icon?: ReactNode;
}

export interface TopbarProps {
  /** Current page's breadcrumb ("Group / Entity / Record") — AppShell
   * supplies this from whatever the active page last set via
   * useSetBreadcrumb. */
  breadcrumb: BreadcrumbSegment[];
  /** FR-UI-07 (KAN-104 item 1): the landing page is the breadcrumb's root —
   * prepended to every page's own segments as a link, and shown alone on the
   * landing page itself. The label comes from the app (no kit string). */
  breadcrumbRoot?: BreadcrumbRoot;
  /** Defaults to a plain `<a>`; pass the router's Link (react-router's
   * `Link` matches this `to`-prop shape), as for the Sidebar. */
  linkComponent?: LinkLike;
  /** Omit only where no cross-entity search exists yet — every real module
   * wires this (§ FR-UI-08). */
  search?: GlobalSearchProps;
}

/**
 * § Topbar. Fixed 60px: breadcrumb left, global search centred, nothing
 * else — account chrome (language, sign out) lives in the sidebar's
 * account cluster, the only place it appears in the product (revised
 * 2026-08-16).
 */
export function Topbar({ breadcrumb, breadcrumbRoot, linkComponent, search }: TopbarProps) {
  return (
    <header
      style={{
        height: 60,
        flexShrink: 0,
        position: "sticky",
        top: 0,
        zIndex: zIndex.stickyBar,
        display: "grid",
        // Equal outer columns keep the centre column (the search box)
        // visually centred on the bar regardless of breadcrumb length,
        // rather than centred on whatever space the breadcrumb leaves.
        gridTemplateColumns: "1fr auto 1fr",
        alignItems: "center",
        gap: spacing.lg,
        padding: `0 ${spacing.xl}`,
        backgroundColor: "rgba(255,255,255,0.85)",
        backdropFilter: "blur(10px)",
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <Breadcrumb segments={withRoot(breadcrumb, breadcrumbRoot)} linkComponent={linkComponent ?? DefaultLink} />
      </div>
      <div style={{ width: 560, maxWidth: "100%" }}>{search && <GlobalSearch {...search} />}</div>
      <div />
    </header>
  );
}

/**
 * KAN-301 — the breadcrumb never wraps. At 1024-1280 px the left grid column
 * is narrow (the centred search takes 560 px), and the record name wrapped
 * onto two lines and slid under the search. Each segment now stays on one
 * line and, when the bar is too narrow, is cut with an ellipsis, its full
 * text in `title`. The leading segments give way first; the record name
 * (the last segment, the one the user is on) shrinks only once they are
 * down to their minimum.
 */
type Crumb = { label: string; href?: string; icon?: ReactNode };

function withRoot(segments: BreadcrumbSegment[], root: BreadcrumbRoot | undefined): Crumb[] {
  const normalised = segments.map((segment) => (typeof segment === "string" ? { label: segment } : segment));
  if (!root) return normalised;
  // The landing page names itself: no second root in front of it.
  if (normalised[0]?.label === root.label) return normalised;
  return [{ label: root.label, href: root.href, icon: root.icon }, ...normalised];
}

function Breadcrumb({ segments, linkComponent: Link }: { segments: Crumb[]; linkComponent: LinkLike }) {
  if (segments.length === 0) return <div />;
  return (
    <div
      data-breadcrumb
      style={{ display: "flex", alignItems: "center", gap: spacing.xs, fontSize: 13, minWidth: 0, flexWrap: "nowrap", overflow: "hidden" }}
    >
      {segments.map(({ label: segment, href, icon }, index) => {
        const isLast = index === segments.length - 1;
        const textStyle: CSSProperties = {
          color: isLast ? slate[9] : slate[5],
          fontWeight: isLast ? 700 : 400,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          minWidth: 0,
        };
        return (
          <span
            key={`${segment}-${index}`}
            data-breadcrumb-segment
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.xs,
              minWidth: icon ? undefined : isLast ? "3em" : "2em",
              flex: icon ? "0 0 auto" : isLast ? "0 1 auto" : "0 1000 auto",
            }}
          >
            {index > 0 && <span style={{ color: slate[3], flexShrink: 0 }}>/</span>}
            {icon && href && !isLast ? (
              <Link
                to={href}
                title={segment}
                aria-label={segment}
                style={{ display: "flex", alignItems: "center", color: slate[5] }}
              >
                <span data-breadcrumb-link aria-hidden="true" style={{ display: "flex" }}>
                  {icon}
                </span>
              </Link>
            ) : href && !isLast ? (
              <Link to={href} style={{ ...textStyle, textDecoration: "none" }}>
                <span title={segment} data-breadcrumb-link>
                  {segment}
                </span>
              </Link>
            ) : (
              <span title={segment} style={textStyle} aria-current={isLast ? "page" : undefined}>
                {segment}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

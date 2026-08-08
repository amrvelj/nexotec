import type { ComponentType, CSSProperties, ReactNode } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Tooltip } from "@mantine/core";
import { purple, radius, slate, spacing } from "../tokens";
import type { NavGroupConfig, NavItemConfig } from "./types";

export const SIDEBAR_WIDTH_EXPANDED = 240;
export const SIDEBAR_WIDTH_COLLAPSED = 64;

type LinkLike = ComponentType<{ to: string; children?: ReactNode; style?: CSSProperties; className?: string }>;

const DefaultLink: LinkLike = ({ to, children, style, className }) => (
  <a href={to} style={style} className={className}>
    {children}
  </a>
);

export interface SidebarProps {
  brand: ReactNode;
  productName: string;
  moduleSubtitle?: string;
  groups: NavGroupConfig[];
  activeHref: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  user?: { name: string; role?: string };
  /** Defaults to a plain `<a>`. Pass your router's Link (e.g. react-router's
   * `Link`, which already matches this `to`-prop shape) for SPA navigation. */
  linkComponent?: LinkLike;
}

/**
 * § Sidebar. "A collapsed sidebar never hides functionality" — every nav
 * item stays reachable, just icon-only with a tooltip. Width/transition/
 * colours all come from tokens, nothing hardcoded here.
 */
export function Sidebar({
  brand,
  productName,
  moduleSubtitle,
  groups,
  activeHref,
  collapsed,
  onToggleCollapsed,
  user,
  linkComponent,
}: SidebarProps) {
  const Link = linkComponent ?? DefaultLink;
  const width = collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED;

  return (
    <nav
      aria-label="Main"
      style={{
        width,
        flexShrink: 0,
        height: "100vh",
        position: "sticky",
        top: 0,
        display: "flex",
        flexDirection: "column",
        backgroundColor: "#fff",
        borderRight: `1px solid ${slate[2]}`,
        transition: "width 160ms ease",
        overflow: "hidden",
      }}
    >
      {/* Brand */}
      <div
        style={{
          height: 60,
          display: "flex",
          alignItems: "center",
          gap: spacing.sm,
          padding: collapsed ? 0 : `0 ${spacing.lg}`,
          justifyContent: collapsed ? "center" : "flex-start",
          borderBottom: `1px solid ${slate[2]}`,
          flexShrink: 0,
        }}
      >
        {brand}
        {!collapsed && (
          <div style={{ opacity: 1, transition: "opacity 60ms linear 100ms", overflow: "hidden" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: slate[9], whiteSpace: "nowrap" }}>
              {productName}
            </div>
            {moduleSubtitle && (
              <div style={{ fontSize: 11, color: slate[5], whiteSpace: "nowrap" }}>{moduleSubtitle}</div>
            )}
          </div>
        )}
      </div>

      {/* Nav groups */}
      <div style={{ flex: 1, overflowY: "auto", padding: `${spacing.sm} 0` }}>
        {groups.map((group) => (
          <div key={group.label} style={{ marginBottom: spacing.sm }}>
            {collapsed ? (
              <div style={{ height: 1, backgroundColor: slate[2], margin: `${spacing.sm} ${spacing.md}` }} />
            ) : (
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.7px",
                  textTransform: "uppercase",
                  color: slate[4],
                  padding: `${spacing.xs} ${spacing.lg}`,
                }}
              >
                {group.label}
              </div>
            )}
            {group.items.map((item) => (
              <NavItem key={item.href} item={item} collapsed={collapsed} active={item.href === activeHref} Link={Link} />
            ))}
          </div>
        ))}
      </div>

      {/* Toggle + user card */}
      <div style={{ borderTop: `1px solid ${slate[2]}`, padding: spacing.sm, flexShrink: 0 }}>
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: collapsed ? "center" : "flex-start",
            gap: spacing.sm,
            width: "100%",
            padding: spacing.sm,
            marginBottom: spacing.xs,
            border: "none",
            background: "none",
            borderRadius: radius.sm,
            color: slate[5],
            cursor: "pointer",
          }}
        >
          {collapsed ? <PanelLeftOpen size={18} strokeWidth={2} /> : <PanelLeftClose size={18} strokeWidth={2} />}
        </button>
        {user && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.sm,
              justifyContent: collapsed ? "center" : "flex-start",
              padding: spacing.xs,
            }}
          >
            <div
              aria-hidden="true"
              style={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                backgroundColor: purple[1],
                color: purple[7],
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {user.name.slice(0, 1).toUpperCase()}
            </div>
            {!collapsed && (
              <div style={{ overflow: "hidden" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: slate[9], whiteSpace: "nowrap" }}>
                  {user.name}
                </div>
                {user.role && (
                  <div style={{ fontSize: 11, color: slate[5], whiteSpace: "nowrap" }}>{user.role}</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </nav>
  );
}

function NavItem({
  item,
  collapsed,
  active,
  Link,
}: {
  item: NavItemConfig;
  collapsed: boolean;
  active: boolean;
  Link: LinkLike;
}) {
  const Icon = item.icon;
  const isSoon = item.status === "soon";

  const content = (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: spacing.sm,
        padding: `${spacing.sm} ${spacing.lg}`,
        margin: collapsed ? `0 ${spacing.xs}` : `0 ${spacing.sm}`,
        borderRadius: radius.sm,
        justifyContent: collapsed ? "center" : "flex-start",
        backgroundColor: active ? purple[0] : "transparent",
        color: active ? purple[7] : slate[6],
        fontWeight: active ? 600 : 400,
        fontSize: 14,
        opacity: isSoon ? 0.42 : 1,
        cursor: isSoon ? "default" : "pointer",
        whiteSpace: "nowrap",
      }}
    >
      <Icon size={18} strokeWidth={2} />
      {!collapsed && <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.label}</span>}
      {!collapsed && isSoon && (
        <span
          style={{
            marginLeft: "auto",
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.5px",
            color: slate[4],
            border: `1px solid ${slate[2]}`,
            borderRadius: radius.full,
            padding: "1px 6px",
          }}
        >
          SOON
        </span>
      )}
    </div>
  );

  const inner = isSoon ? (
    <div aria-disabled="true">{content}</div>
  ) : (
    <Link to={item.href} style={{ textDecoration: "none", display: "block" }}>
      {content}
    </Link>
  );

  if (collapsed) {
    return (
      <Tooltip label={item.label} position="right" openDelay={400} withArrow>
        <div>{inner}</div>
      </Tooltip>
    );
  }
  return inner;
}

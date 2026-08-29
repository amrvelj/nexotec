import type { ComponentType, CSSProperties, ReactNode } from "react";
import { Bell, Building2, Check, LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Menu, SegmentedControl, Tooltip, UnstyledButton } from "@mantine/core";
import { purple, radius, slate, spacing, white } from "../tokens";
import type { NavGroupConfig, NavItemConfig } from "./types";

export const SIDEBAR_WIDTH_EXPANDED = 240;
export const SIDEBAR_WIDTH_COLLAPSED = 64;

export type UiLanguage = "de" | "fr" | "it" | "en";

type LinkLike = ComponentType<{ to: string; children?: ReactNode; style?: CSSProperties; className?: string }>;

const DefaultLink: LinkLike = ({ to, children, style, className }) => (
  <a href={to} style={style} className={className}>
    {children}
  </a>
);

export interface DealershipSummary {
  id: string;
  legalName: string;
}

export interface SidebarProps {
  brand: ReactNode;
  productName: string;
  moduleSubtitle?: string;
  groups: NavGroupConfig[];
  activeHref: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  user?: { name: string; email: string; role?: string };
  uiLanguage: UiLanguage;
  onLanguageChange: (language: UiLanguage) => void;
  onSignOut: () => void;
  /** Translated "Sign out" label — defaults to English. */
  signOutLabel?: string;
  /** The dealership the current session is acting as. */
  activeDealership?: DealershipSummary;
  /** Every dealership this user may switch to, including activeDealership
   * itself (WP-3 PR-3). The switcher renders only when this holds more
   * than one entry — the common case is a single membership, where a
   * permanent switcher control would be clutter for no function. */
  memberships?: DealershipSummary[];
  onSwitchDealership?: (dealershipId: string) => void;
  /** Defaults to a plain `<a>`. Pass your router's Link (e.g. react-router's
   * `Link`, which already matches this `to`-prop shape) for SPA navigation. */
  linkComponent?: LinkLike;
}

/**
 * § Sidebar. "A collapsed sidebar never hides functionality" — every nav
 * item stays reachable, just icon-only with a tooltip. Width/transition/
 * colours all come from tokens, nothing hardcoded here.
 *
 * § Account cluster (revised 2026-08-16): sits directly above the collapse
 * toggle and is the ONLY place account chrome appears in the product — the
 * topbar carries the breadcrumb and nothing else. Three rows when expanded:
 * UI language, notifications (Phase C, disabled), and the user card opening
 * a menu (profile/preferences/dealership switcher/sign out). Collapsed:
 * avatar only, centred — clicking it opens the same menu with language and
 * notifications promoted into it as submenus/items, so nothing is
 * unreachable collapsed that was reachable expanded (WP-3 PR-3 adds the
 * dealership switcher on this same contract).
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
  uiLanguage,
  onLanguageChange,
  onSignOut,
  signOutLabel = "Sign out",
  activeDealership,
  memberships,
  onSwitchDealership,
  linkComponent,
}: SidebarProps) {
  const Link = linkComponent ?? DefaultLink;
  const width = collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED;
  const canSwitchDealership = Boolean(memberships && memberships.length > 1 && onSwitchDealership);

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
        backgroundColor: white,
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
        {groups.map((group, groupIndex) => (
          <div key={group.label ?? `group-${groupIndex}`} style={{ marginBottom: spacing.sm }}>
            {group.label ? (
              collapsed ? (
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
              )
            ) : null}
            {group.items.map((item) => (
              <NavItem key={item.href} item={item} collapsed={collapsed} active={item.href === activeHref} Link={Link} />
            ))}
          </div>
        ))}
      </div>

      {/* Toggle + account cluster */}
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
            border: "none",
            background: "none",
            borderRadius: radius.sm,
            color: slate[5],
            cursor: "pointer",
          }}
        >
          {collapsed ? <PanelLeftOpen size={18} strokeWidth={2} /> : <PanelLeftClose size={18} strokeWidth={2} />}
        </button>

        {!collapsed && (
          <div
            style={{
              marginTop: spacing.sm,
              paddingTop: spacing.sm,
              borderTop: `1px solid ${slate[2]}`,
              display: "flex",
              flexDirection: "column",
              gap: spacing.xs,
            }}
          >
            <SegmentedControl
              size="xs"
              fullWidth
              value={uiLanguage}
              onChange={(value) => onLanguageChange(value as UiLanguage)}
              data={[
                { label: "DE", value: "de" },
                { label: "FR", value: "fr" },
                { label: "IT", value: "it" },
                { label: "EN", value: "en" },
              ]}
            />
            {/* Notifications: Phase C — visible now so its place in the
                cluster is settled, disabled until it has something to show,
                same SOON convention as an unbuilt nav module. */}
            <div
              aria-disabled="true"
              style={{
                display: "flex",
                alignItems: "center",
                gap: spacing.sm,
                width: "100%",
                padding: `7px ${spacing.sm}`,
                borderRadius: radius.sm,
                color: slate[4],
                fontSize: 13,
                cursor: "default",
              }}
            >
              <Bell size={16} strokeWidth={2} />
              <span>Notifications</span>
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 9,
                  fontWeight: 700,
                  letterSpacing: "0.5px",
                  border: `1px solid ${slate[2]}`,
                  borderRadius: radius.full,
                  padding: "1px 6px",
                }}
              >
                SOON
              </span>
            </div>
          </div>
        )}

        {user && (
          <Menu shadow="md" width={240} position="right-end" offset={8}>
            <Menu.Target>
              <UnstyledButton
                aria-label="Account menu"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: spacing.sm,
                  width: "100%",
                  justifyContent: collapsed ? "center" : "flex-start",
                  padding: spacing.xs,
                  marginTop: collapsed ? spacing.xs : 0,
                  borderRadius: radius.sm,
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
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{user.email}</Menu.Label>

              {/* Collapsed sidebar promotes language + notifications into
                  this menu — nothing reachable expanded goes unreachable. */}
              {collapsed && (
                <>
                  <Menu.Divider />
                  <Menu.Label>Language</Menu.Label>
                  <div style={{ padding: `0 ${spacing.sm} ${spacing.xs}` }}>
                    <SegmentedControl
                      size="xs"
                      fullWidth
                      value={uiLanguage}
                      onChange={(value) => onLanguageChange(value as UiLanguage)}
                      data={[
                        { label: "DE", value: "de" },
                        { label: "FR", value: "fr" },
                        { label: "IT", value: "it" },
                        { label: "EN", value: "en" },
                      ]}
                    />
                  </div>
                  <Menu.Item leftSection={<Bell size={16} />} disabled>
                    Notifications (soon)
                  </Menu.Item>
                </>
              )}

              {canSwitchDealership && activeDealership && (
                <>
                  <Menu.Divider />
                  <Menu.Label>Switch dealership</Menu.Label>
                  {memberships!.map((dealership) => (
                    <Menu.Item
                      key={dealership.id}
                      leftSection={<Building2 size={16} />}
                      rightSection={dealership.id === activeDealership.id ? <Check size={14} /> : null}
                      disabled={dealership.id === activeDealership.id}
                      onClick={() => onSwitchDealership?.(dealership.id)}
                    >
                      {dealership.legalName}
                    </Menu.Item>
                  ))}
                </>
              )}

              <Menu.Divider />
              <Menu.Item leftSection={<LogOut size={16} />} onClick={onSignOut}>
                {signOutLabel}
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
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

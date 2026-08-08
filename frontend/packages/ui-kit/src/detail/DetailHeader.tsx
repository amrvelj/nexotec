import type { ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";
import { ActionIcon, Group, Menu } from "@mantine/core";
import { purple, radius, shadow, slate, spacing, typography } from "../tokens";

export interface DetailHeaderProps {
  /** 54px square — an icon, initials circle, or similar entity mark. */
  entityMark: ReactNode;
  title: string;
  /** The business key (e.g. customer number), rendered in mono. */
  businessKey: string;
  /** Status/type badge row, rendered directly below the title. */
  badges?: ReactNode;
  primaryAction?: ReactNode;
  secondaryActions?: ReactNode;
  /** Menu.Item elements — "the same items as the grid row menu". Omit to
   * hide the overflow trigger entirely. */
  overflowItems?: ReactNode;
}

/**
 * § UI/UX Core Principles — Detail Screens, "Identity header" row. "The
 * Customer 360 pattern generalises to every entity. Every detail screen
 * in the DMS has this shape" — so this carries no customer-specific
 * knowledge; the caller supplies the entity mark, title, key and badges.
 */
export function DetailHeader({ entityMark, title, businessKey, badges, primaryAction, secondaryActions, overflowItems }: DetailHeaderProps) {
  return (
    <div
      style={{
        borderRadius: radius.lg,
        boxShadow: shadow.sm,
        backgroundColor: "#fff",
        overflow: "hidden",
        border: `1px solid ${slate[2]}`,
      }}
    >
      <div style={{ height: 3, background: `linear-gradient(90deg, ${purple[6]}, ${purple[4]})` }} />
      <div style={{ display: "flex", alignItems: "center", gap: spacing.lg, padding: spacing.lg }}>
        <div
          style={{
            width: 54,
            height: 54,
            borderRadius: radius.lg,
            backgroundColor: purple[1],
            color: purple[7],
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            fontSize: 20,
            fontWeight: 700,
          }}
        >
          {entityMark}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: spacing.sm, flexWrap: "wrap" }}>
            <span style={{ fontSize: typography.pageTitle.size, fontWeight: typography.pageTitle.weight, letterSpacing: typography.pageTitle.letterSpacing, color: slate[9] }}>
              {title}
            </span>
            <span style={{ fontSize: typography.mono.size, fontFamily: typography.mono.family, color: slate[5] }}>{businessKey}</span>
          </div>
          {badges && (
            <Group gap="xs" mt={6}>
              {badges}
            </Group>
          )}
        </div>
        <Group gap="sm" wrap="nowrap">
          {secondaryActions}
          {primaryAction}
          {overflowItems && (
            <Menu shadow="md" width={200} position="bottom-end" withinPortal>
              <Menu.Target>
                <ActionIcon variant="subtle" color="gray" size="lg" aria-label="More actions">
                  <MoreHorizontal size={18} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>{overflowItems}</Menu.Dropdown>
            </Menu>
          )}
        </Group>
      </div>
    </div>
  );
}

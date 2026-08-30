import type { ReactNode } from "react";
import { ActionIcon, Button, Group, Tooltip } from "@mantine/core";
import { MoreHorizontal } from "lucide-react";
import { purple, radius, shadow, slate, spacing, typography, white } from "../tokens";
import { RowMenu, type RowMenuGroups } from "../components/RowMenu";

export interface DetailHeaderAction {
  label: string;
  icon?: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** Shown in a tooltip when disabled — ADR-061's own "disabled items
   * shown-and-explained, never hidden" applies to the header's own two
   * buttons exactly as much as it does to the overflow's menu items. */
  disabledReason?: string;
}

export interface DetailHeaderProps {
  /** 54px square — an icon, initials circle, or similar entity mark. */
  entityMark: ReactNode;
  title: string;
  /** The business key (e.g. customer number), rendered in mono. */
  businessKey: string;
  /** Status/type badge row, rendered directly below the title. */
  badges?: ReactNode;
  /** § ADR-061 — exactly one primary action. Most entities have at least
   * one ("Edit", "Create offer"); omit only for the rare one that
   * genuinely doesn't. */
  primaryAction?: DetailHeaderAction;
  /** § ADR-061 — exactly one alternative, rendered beside the primary.
   * Anything beyond these two belongs in `overflowActions`, never a third
   * button here. */
  alternativeAction?: DetailHeaderAction;
  /** § ADR-061 — "the overflow carries the entity's full row menu — same
   * items, same order, same disabled-with-explanation entries." The exact
   * same `RowMenuGroups` shape a grid's own `rowActions` returns, rendered
   * through the same `RowMenu` component — never a second, hand-rolled
   * menu that can drift from the grid's. */
  overflowActions?: RowMenuGroups;
}

/**
 * § UI/UX Core Principles — Detail Screens, "Identity header" row. "The
 * Customer 360 pattern generalises to every entity. Every detail screen
 * in the DMS has this shape" — so this carries no customer-specific
 * knowledge; the caller supplies the entity mark, title, key and badges.
 */
export function DetailHeader({
  entityMark,
  title,
  businessKey,
  badges,
  primaryAction,
  alternativeAction,
  overflowActions,
}: DetailHeaderProps) {
  return (
    <div
      style={{
        borderRadius: radius.lg,
        boxShadow: shadow.sm,
        backgroundColor: white,
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
          {alternativeAction && <HeaderActionButton action={alternativeAction} variant="default" />}
          {primaryAction && <HeaderActionButton action={primaryAction} variant="filled" />}
          {overflowActions && (
            <RowMenu
              groups={overflowActions}
              ariaLabel="More actions"
              trigger={
                <ActionIcon variant="subtle" color="gray" size="lg" aria-label="More actions">
                  <MoreHorizontal size={18} />
                </ActionIcon>
              }
            />
          )}
        </Group>
      </div>
    </div>
  );
}

function HeaderActionButton({ action, variant }: { action: DetailHeaderAction; variant: "filled" | "default" }) {
  const button = (
    <Button variant={variant} color="violet" leftSection={action.icon} onClick={action.onClick} disabled={action.disabled}>
      {action.label}
    </Button>
  );
  if (action.disabled && action.disabledReason) {
    // A native `disabled` button fires no pointer events at all, so a
    // Tooltip anchored directly to it would never see the hover that
    // should reveal the reason — the standard fix is a plain wrapper
    // element (which still receives pointer events) as the actual anchor.
    return (
      <Tooltip label={action.disabledReason}>
        <span style={{ display: "inline-block" }}>{button}</span>
      </Tooltip>
    );
  }
  return button;
}

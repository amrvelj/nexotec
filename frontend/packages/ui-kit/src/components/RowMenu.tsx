import { Fragment, type ReactNode } from "react";
import { Menu } from "@mantine/core";
import { MoreHorizontal } from "lucide-react";
import { radius, semantic, slate } from "../tokens";

export interface RowMenuAction {
  label: string;
  icon: ReactNode;
  onClick: () => void;
  /** Shown-and-explained, never hidden (§ ADR-061 / The Data Grid — row
   * menu). A disabled item with no reason is a defect, not a shortcut. */
  disabled?: boolean;
  disabledReason?: string;
}

/**
 * § ADR-061 — "the row menu is the single definition of what can be done
 * to an entity; both surfaces render from it." The five groups, always in
 * this order, a divider between any two non-empty ones, an empty group
 * simply omitted (no heading, no divider for it). `destructive` gets its
 * own colour treatment automatically — that IS the reason it is its own
 * group rather than the last few items of `exportPrint`.
 */
export interface RowMenuGroups {
  navigate?: RowMenuAction[];
  edit?: RowMenuAction[];
  createFrom?: RowMenuAction[];
  exportPrint?: RowMenuAction[];
  destructive?: RowMenuAction[];
}

export interface RowMenuProps {
  groups: RowMenuGroups;
  ariaLabel: string;
  /** Defaults to the grid's own `⋯` icon-button trigger. A detail screen's
   * overflow (§ ADR-061: "one primary, one alternative, and an overflow
   * carrying the entity's full row menu") passes its own trigger here —
   * same groups, same order, same disabled-with-explanation entries,
   * because both surfaces render from this one component. */
  trigger?: ReactNode;
}

const GROUP_ORDER: (keyof RowMenuGroups)[] = ["navigate", "edit", "createFrom", "exportPrint", "destructive"];

export function RowMenu({ groups, ariaLabel, trigger }: RowMenuProps) {
  const sections = GROUP_ORDER.map((key) => ({ key, actions: groups[key] ?? [], destructive: key === "destructive" })).filter(
    (section) => section.actions.length > 0
  );

  return (
    <Menu shadow="md" width={220} position="bottom-end" withinPortal>
      <Menu.Target>{trigger ?? <DefaultTrigger ariaLabel={ariaLabel} />}</Menu.Target>
      <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
        {sections.map((section, index) => (
          <Fragment key={section.key}>
            {index > 0 && <Menu.Divider />}
            {section.actions.map((action) => (
              <Menu.Item
                key={action.label}
                leftSection={action.icon}
                disabled={action.disabled}
                onClick={action.disabled ? undefined : action.onClick}
                color={section.destructive ? "red" : undefined}
              >
                <div>
                  <div>{action.label}</div>
                  {action.disabled && action.disabledReason && (
                    <div style={{ fontSize: 11, color: slate[4], fontWeight: 400 }}>{action.disabledReason}</div>
                  )}
                </div>
              </Menu.Item>
            ))}
          </Fragment>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

function DefaultTrigger({ ariaLabel }: { ariaLabel: string }) {
  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
      }}
      style={{
        border: "none",
        background: "none",
        cursor: "pointer",
        color: slate[4],
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 28,
        height: 28,
        borderRadius: radius.sm,
      }}
    >
      <MoreHorizontal size={18} />
    </button>
  );
}

// Re-exported so a caller that wants the destructive colour token to match
// this menu's own (e.g. a confirm dialog after clicking a destructive
// action) doesn't have to guess which semantic entry it used.
export const ROW_MENU_DESTRUCTIVE_COLOR = semantic.destructive.text;

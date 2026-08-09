import { LogOut } from "lucide-react";
import { Menu, SegmentedControl, UnstyledButton } from "@mantine/core";
import { purple, slate, spacing } from "../tokens";

export type UiLanguage = "de" | "fr" | "it" | "en";

export interface TopbarProps {
  user: { name: string; email: string };
  uiLanguage: UiLanguage;
  onLanguageChange: (language: UiLanguage) => void;
  onSignOut: () => void;
  /** Current page's breadcrumb ("Group / Entity / Record") — AppShell
   * supplies this from whatever the active page last set via
   * useSetBreadcrumb. */
  breadcrumb: string[];
  /** Translated "Sign out" label — defaults to English. */
  signOutLabel?: string;
}

/**
 * § Topbar. Fixed 60px, breadcrumb left, UI language switcher + user menu
 * right. "The topbar carries no page actions. Page actions live in the
 * action bar" — deliberately no primary-action button here, ever.
 *
 * Notifications bell and the ⌘K command palette slot are both explicitly
 * Phase C in the source doc — not included yet.
 */
export function Topbar({ user, uiLanguage, onLanguageChange, onSignOut, breadcrumb, signOutLabel = "Sign out" }: TopbarProps) {
  return (
    <header
      style={{
        height: 60,
        flexShrink: 0,
        position: "sticky",
        top: 0,
        zIndex: 10,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: `0 ${spacing.xl}`,
        backgroundColor: "rgba(255,255,255,0.85)",
        backdropFilter: "blur(10px)",
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      <Breadcrumb segments={breadcrumb} />

      <div style={{ display: "flex", alignItems: "center", gap: spacing.md }}>
        <SegmentedControl
          size="xs"
          value={uiLanguage}
          onChange={(value) => onLanguageChange(value as UiLanguage)}
          data={[
            { label: "DE", value: "de" },
            { label: "FR", value: "fr" },
            { label: "IT", value: "it" },
            { label: "EN", value: "en" },
          ]}
        />

        <Menu shadow="md" width={200} position="bottom-end">
          <Menu.Target>
            <UnstyledButton aria-label="User menu">
              <div
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: "50%",
                  backgroundColor: purple[1],
                  color: purple[7],
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                {user.name.slice(0, 1).toUpperCase()}
              </div>
            </UnstyledButton>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>{user.email}</Menu.Label>
            <Menu.Divider />
            <Menu.Item leftSection={<LogOut size={16} />} onClick={onSignOut}>
              {signOutLabel}
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </div>
    </header>
  );
}

function Breadcrumb({ segments }: { segments: string[] }) {
  if (segments.length === 0) return <div />;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.xs, fontSize: 13 }}>
      {segments.map((segment, index) => {
        const isLast = index === segments.length - 1;
        return (
          <span key={`${segment}-${index}`} style={{ display: "flex", alignItems: "center", gap: spacing.xs }}>
            {index > 0 && <span style={{ color: slate[3] }}>/</span>}
            <span style={{ color: isLast ? slate[9] : slate[5], fontWeight: isLast ? 700 : 400 }}>{segment}</span>
          </span>
        );
      })}
    </div>
  );
}

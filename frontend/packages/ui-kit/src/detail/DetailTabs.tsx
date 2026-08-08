import { purple, radius, slate, spacing } from "../tokens";
import type { DetailTab } from "./types";

export interface DetailTabsProps {
  tabs: DetailTab[];
  activeTab: string;
  onTabChange: (id: string) => void;
}

/**
 * § UI/UX Core Principles — Detail Screens, "Tabs" row. "Underline style,
 * purple.6 active underline. Each tab shows a count badge where the tab
 * holds a collection. Tabs are horizontally scrollable, never wrapped to
 * two rows." URL sync (`?tab=vehicles`) is the caller's job — this
 * component is a plain controlled tab bar so it doesn't have to know
 * which router the host app uses.
 */
export function DetailTabs({ tabs, activeTab, onTabChange }: DetailTabsProps) {
  return (
    <div
      role="tablist"
      style={{
        display: "flex",
        gap: spacing.lg,
        overflowX: "auto",
        overflowY: "hidden",
        whiteSpace: "nowrap",
        borderBottom: `1px solid ${slate[2]}`,
      }}
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onTabChange(tab.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              flexShrink: 0,
              border: "none",
              background: "none",
              cursor: "pointer",
              padding: `${spacing.sm} 2px`,
              marginBottom: -1,
              borderBottom: `2px solid ${isActive ? purple[6] : "transparent"}`,
              fontSize: 14,
              fontWeight: isActive ? 600 : 500,
              color: isActive ? slate[9] : slate[5],
            }}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: isActive ? purple[7] : slate[5],
                  backgroundColor: isActive ? purple[1] : slate[1],
                  borderRadius: radius.full,
                  padding: "1px 6px",
                  minWidth: 18,
                  textAlign: "center",
                }}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

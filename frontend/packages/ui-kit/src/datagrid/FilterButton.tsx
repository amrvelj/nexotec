import type { ReactNode } from "react";
import { ListFilter } from "lucide-react";
import { Popover } from "@mantine/core";
import { purple, radius, slate, spacing, typography, white } from "../tokens";

export interface FilterButtonProps {
  /** Number of currently active filters — drives the purple.6 count badge. */
  activeCount: number;
  /** The filter fields themselves, supplied by the app layer. */
  children: ReactNode;
  opened: boolean;
  onChange: (opened: boolean) => void;
  /** Translated "Filter" label — defaults to English. */
  label?: string;
}

/**
 * § Action Bar, Zone 3 — "Filter icon + 'Filter' + active-count badge. The
 * count badge is purple.6 when > 0. Opens a popover with structured
 * filters. Never a separate page." This is the generic trigger + popover
 * shell; the actual filter fields are app-specific and passed as children.
 */
export function FilterButton({ activeCount, children, opened, onChange, label = "Filter" }: FilterButtonProps) {
  return (
    <Popover opened={opened} onChange={onChange} position="bottom-start" shadow="md" withArrow>
      <Popover.Target>
        <button
          type="button"
          onClick={() => onChange(!opened)}
          aria-label={label}
          style={{
            display: "flex",
            alignItems: "center",
            gap: spacing.xs,
            height: 36,
            padding: `0 ${spacing.sm}`,
            border: `1px solid ${slate[3]}`,
            borderRadius: radius.md,
            background: opened ? purple[0] : white,
            color: slate[7],
            fontSize: typography.body.size,
            fontWeight: typography.bodyStrong.weight,
            cursor: "pointer",
          }}
        >
          <ListFilter size={16} color={slate[5]} />
          {label}
          {activeCount > 0 && (
            <span
              aria-hidden="true"
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                minWidth: 18,
                height: 18,
                padding: "0 5px",
                borderRadius: radius.full,
                backgroundColor: purple[6],
                color: white,
                fontSize: 11,
                fontWeight: 700,
                lineHeight: 1,
              }}
            >
              {activeCount}
            </span>
          )}
        </button>
      </Popover.Target>
      <Popover.Dropdown>
        <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm, minWidth: 260 }}>{children}</div>
      </Popover.Dropdown>
    </Popover>
  );
}

import { useState } from "react";
import { Popover } from "@mantine/core";
import { Columns3, GripVertical, Lock, Pin, PinOff, RotateCcw, Search } from "lucide-react";
import { purple, radius, slate, spacing, white } from "../tokens";
import {
  reorderColumn,
  resetColumnLayout,
  toggleColumnVisibility,
  togglePinned,
  type ColumnLayoutState,
  type ColumnRegistryEntry,
} from "./columnLayout";

export interface ColumnPreset {
  label: string;
  /** Column ids visible when this preset is applied — everything else in
   * the registry is hidden, order unchanged. */
  visibleIds: string[];
}

export interface ColumnConfigPanelProps {
  registry: ColumnRegistryEntry[];
  layout: ColumnLayoutState;
  onLayoutChange: (layout: ColumnLayoutState) => void;
  presets?: ColumnPreset[];
  labels?: {
    trigger?: string;
    search?: string;
    reset?: string;
    presets?: string;
    lockedTooltip?: string;
  };
}

const DEFAULT_LABELS = {
  trigger: "Columns",
  search: "Find a column…",
  reset: "Reset to default",
  presets: "Presets",
  lockedTooltip: "Always shown",
};

/**
 * § Columns — the 320px non-modal side panel: search, show/hide, drag-
 * reorder, pin, group presets, reset. "Non-modal" — a `Popover`, not a
 * `Modal` — so the grid underneath stays interactive while this is open,
 * unlike ADR-059's Overlay (a different, full-screen contract for a
 * different job). Resize lives on the header's own edge-drag, not here —
 * see `HeaderCell`'s resize handle in DataGrid.tsx.
 */
export function ColumnConfigPanel({ registry, layout, onLayoutChange, presets, labels }: ColumnConfigPanelProps) {
  const L = { ...DEFAULT_LABELS, ...labels };
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState("");
  const [draggedId, setDraggedId] = useState<string | null>(null);

  const byId = new Map(registry.map((c) => [c.id, c]));
  const visibleQuery = query.trim().toLowerCase();
  const orderedEntries = layout.order
    .map((id) => byId.get(id))
    .filter((entry): entry is ColumnRegistryEntry => Boolean(entry))
    .filter((entry) => !visibleQuery || entry.label.toLowerCase().includes(visibleQuery));

  const move = (id: string, direction: -1 | 1) => {
    const index = layout.order.indexOf(id);
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= layout.order.length) return;
    // Moving down means "insert before whatever is now two spots ahead";
    // moving up means "insert before the thing currently just above it".
    const beforeId = direction === -1 ? layout.order[targetIndex] : (layout.order[targetIndex + 1] ?? null);
    onLayoutChange(reorderColumn(layout, id, beforeId));
  };

  return (
    <Popover opened={opened} onChange={setOpened} width={320} position="bottom-end" shadow="md" withinPortal>
      <Popover.Target>
        <button
          type="button"
          onClick={() => setOpened((o) => !o)}
          aria-label={L.trigger}
          style={{
            width: 32,
            height: 32,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "none",
            background: opened ? purple[0] : "none",
            borderRadius: radius.sm,
            color: opened ? purple[7] : slate[6],
            cursor: "pointer",
          }}
        >
          <Columns3 size={18} />
        </button>
      </Popover.Target>
      <Popover.Dropdown style={{ padding: 0 }}>
        <div style={{ padding: spacing.sm, borderBottom: `1px solid ${slate[2]}`, position: "relative" }}>
          <Search size={14} color={slate[4]} style={{ position: "absolute", left: 20, top: "50%", transform: "translateY(-50%)" }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={L.search}
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: `${spacing.xs} ${spacing.xs} ${spacing.xs} 28px`,
              border: `1px solid ${slate[2]}`,
              borderRadius: radius.sm,
              fontSize: 13,
            }}
          />
        </div>

        {presets && presets.length > 0 && (
          <div style={{ padding: spacing.sm, borderBottom: `1px solid ${slate[2]}`, display: "flex", flexWrap: "wrap", gap: spacing.xs }}>
            {presets.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() =>
                  onLayoutChange({
                    ...layout,
                    hidden: registry.filter((c) => !preset.visibleIds.includes(c.id) && !c.locked).map((c) => c.id),
                  })
                }
                style={{
                  border: `1px solid ${slate[2]}`,
                  borderRadius: radius.full,
                  padding: `2px ${spacing.sm}`,
                  fontSize: 12,
                  background: white,
                  cursor: "pointer",
                  color: slate[6],
                }}
              >
                {preset.label}
              </button>
            ))}
          </div>
        )}

        <div role="list" style={{ maxHeight: 320, overflowY: "auto", padding: spacing.xs }}>
          {orderedEntries.map((entry, index) => {
            const isHidden = layout.hidden.includes(entry.id);
            const isPinned = layout.pinnedLeft.includes(entry.id);
            return (
              <div
                key={entry.id}
                role="listitem"
                draggable={!visibleQuery}
                onDragStart={() => setDraggedId(entry.id)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (draggedId && draggedId !== entry.id) onLayoutChange(reorderColumn(layout, draggedId, entry.id));
                  setDraggedId(null);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: spacing.xs,
                  padding: `${spacing.xs} ${spacing.sm}`,
                  borderRadius: radius.sm,
                  opacity: draggedId === entry.id ? 0.4 : 1,
                }}
              >
                <span style={{ color: slate[3], cursor: visibleQuery ? "default" : "grab", display: "flex" }} aria-hidden="true">
                  <GripVertical size={14} />
                </span>

                <label style={{ display: "flex", alignItems: "center", gap: spacing.xs, flex: 1, minWidth: 0, fontSize: 13, color: slate[7] }}>
                  <input
                    type="checkbox"
                    checked={!isHidden}
                    disabled={entry.locked}
                    onChange={() => onLayoutChange(toggleColumnVisibility(layout, entry.id, Boolean(entry.locked)))}
                  />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{entry.label}</span>
                  {entry.locked && (
                    <span title={L.lockedTooltip} aria-label={L.lockedTooltip} style={{ display: "flex", color: slate[4] }}>
                      <Lock size={14} />
                    </span>
                  )}
                </label>

                <button
                  type="button"
                  onClick={() => onLayoutChange(togglePinned(layout, entry.id))}
                  aria-label={isPinned ? "Unpin column" : "Pin column"}
                  aria-pressed={isPinned}
                  disabled={entry.locked}
                  style={{
                    border: "none",
                    background: "none",
                    cursor: entry.locked ? "default" : "pointer",
                    color: isPinned ? purple[6] : slate[3],
                    display: "flex",
                    opacity: entry.locked ? 0.3 : 1,
                  }}
                >
                  {isPinned ? <PinOff size={14} /> : <Pin size={14} />}
                </button>

                {/* Keyboard/no-drag fallback for reorder — the grip handle
                    above needs a pointer; these two never do. */}
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <button
                    type="button"
                    onClick={() => move(entry.id, -1)}
                    disabled={index === 0}
                    aria-label={`Move ${entry.label} up`}
                    style={{ border: "none", background: "none", cursor: "pointer", color: slate[4], lineHeight: 0.6, fontSize: 10 }}
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    onClick={() => move(entry.id, 1)}
                    disabled={index === orderedEntries.length - 1}
                    aria-label={`Move ${entry.label} down`}
                    style={{ border: "none", background: "none", cursor: "pointer", color: slate[4], lineHeight: 0.6, fontSize: 10 }}
                  >
                    ▼
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ padding: spacing.sm, borderTop: `1px solid ${slate[2]}` }}>
          <button
            type="button"
            onClick={() => onLayoutChange(resetColumnLayout(registry))}
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.xs,
              border: "none",
              background: "none",
              cursor: "pointer",
              color: slate[6],
              fontSize: 13,
            }}
          >
            <RotateCcw size={14} />
            {L.reset}
          </button>
        </div>
      </Popover.Dropdown>
    </Popover>
  );
}

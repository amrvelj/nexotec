import { useState } from "react";
import { Popover } from "@mantine/core";
import { Check, ChevronDown, Plus, Star, Trash2 } from "lucide-react";
import { purple, radius, slate, spacing, typography, white } from "../tokens";
import { FilterBuilder, type FilterBuilderProps } from "./FilterBuilder";
import type { FilterFieldDef, FilterPredicate } from "./filterPredicate";
import type { SavedView } from "./savedView";

export interface ViewsAndFiltersProps {
  /** The trigger button's own label — "( ▾ All customers )" — always the
   * currently-applied view's name, "All <entities>" when none is applied. */
  currentViewName: string;
  views: SavedView[];
  onApplyView: (view: SavedView) => void;
  onSaveCurrentAsView: (name: string) => void;
  onDeleteView: (id: string) => void;
  onSetDefaultView: (id: string | null) => void;

  fields: FilterFieldDef[];
  predicates: FilterPredicate[];
  onPredicatesChange: (predicates: FilterPredicate[]) => void;
  onPreviewCount?: FilterBuilderProps["onPreviewCount"];
  onResetFilters: () => void;

  labels?: {
    viewsHeading?: string;
    saveCurrentView?: string;
    savePlaceholder?: string;
    manageFiltersHeading?: string;
    reset?: string;
    setDefault?: string;
    unsetDefault?: string;
  };
}

const DEFAULT_LABELS = {
  viewsHeading: "Views",
  saveCurrentView: "Save current view as…",
  savePlaceholder: "View name",
  manageFiltersHeading: "Manage filters",
  reset: "Reset",
  setDefault: "Set as default",
  unsetDefault: "Remove as default",
};

/**
 * § ADR-058 — "One button, one panel, two sections." Replaces the old
 * bare `FilterButton` popover entirely; a Notion page's own "Zones" table
 * shows a separate view-selector button next to it, which is the stale
 * content ADR-058 itself supersedes — this component is the one button.
 */
export function ViewsAndFilters({
  currentViewName,
  views,
  onApplyView,
  onSaveCurrentAsView,
  onDeleteView,
  onSetDefaultView,
  fields,
  predicates,
  onPredicatesChange,
  onPreviewCount,
  onResetFilters,
  labels,
}: ViewsAndFiltersProps) {
  const L = { ...DEFAULT_LABELS, ...labels };
  const [opened, setOpened] = useState(false);
  const [savingAs, setSavingAs] = useState(false);
  const [newViewName, setNewViewName] = useState("");

  const commitSave = () => {
    const name = newViewName.trim();
    if (!name) return;
    onSaveCurrentAsView(name);
    setNewViewName("");
    setSavingAs(false);
  };

  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-start" shadow="md" width={340} withinPortal>
      <Popover.Target>
        <button
          type="button"
          onClick={() => setOpened((o) => !o)}
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
            maxWidth: 220,
          }}
        >
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{currentViewName}</span>
          {predicates.length > 0 && (
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
              {predicates.length}
            </span>
          )}
          <ChevronDown size={14} color={slate[4]} style={{ marginLeft: "auto" }} />
        </button>
      </Popover.Target>

      <Popover.Dropdown style={{ padding: 0 }}>
        <div style={{ padding: spacing.sm, borderBottom: `1px solid ${slate[2]}` }}>
          <SectionHeading>{L.viewsHeading}</SectionHeading>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {views.map((view) => (
              <div
                key={view.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: spacing.xs,
                  padding: `${spacing.xs} ${spacing.xs}`,
                  borderRadius: radius.sm,
                  cursor: "pointer",
                  backgroundColor: view.name === currentViewName ? purple[0] : undefined,
                }}
                onClick={() => onApplyView(view)}
              >
                {view.name === currentViewName ? <Check size={14} color={purple[6]} /> : <span style={{ width: 14 }} />}
                <span style={{ flex: 1, fontSize: 13, color: slate[7] }}>{view.name}</span>
                <IconButton
                  ariaLabel={view.isDefault ? L.unsetDefault : L.setDefault}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSetDefaultView(view.isDefault ? null : view.id);
                  }}
                >
                  <Star size={13} fill={view.isDefault ? purple[6] : "none"} color={view.isDefault ? purple[6] : slate[4]} />
                </IconButton>
                <IconButton
                  ariaLabel="Delete view"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteView(view.id);
                  }}
                >
                  <Trash2 size={13} />
                </IconButton>
              </div>
            ))}
          </div>

          {savingAs ? (
            <div style={{ display: "flex", gap: spacing.xs, marginTop: spacing.xs }}>
              <input
                autoFocus
                value={newViewName}
                onChange={(e) => setNewViewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && commitSave()}
                placeholder={L.savePlaceholder}
                style={{ flex: 1, border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "4px 6px", fontSize: 13 }}
              />
              <button type="button" onClick={commitSave} style={{ border: "none", background: "none", color: purple[6], fontWeight: 700, cursor: "pointer" }}>
                <Check size={16} />
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setSavingAs(true)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 4,
                border: "none",
                background: "none",
                cursor: "pointer",
                color: purple[6],
                fontWeight: 600,
                fontSize: 13,
                marginTop: spacing.xs,
              }}
            >
              <Plus size={14} />
              {L.saveCurrentView}
            </button>
          )}
        </div>

        <div style={{ padding: spacing.sm }}>
          <SectionHeading>{L.manageFiltersHeading}</SectionHeading>
          <FilterBuilder fields={fields} predicates={predicates} onChange={onPredicatesChange} onPreviewCount={onPreviewCount} />
          {predicates.length > 0 && (
            <button
              type="button"
              onClick={onResetFilters}
              style={{ border: "none", background: "none", cursor: "pointer", color: slate[5], fontSize: 13, marginTop: spacing.sm, padding: 0 }}
            >
              {L.reset}
            </button>
          )}
        </div>
      </Popover.Dropdown>
    </Popover>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.7px",
        textTransform: "uppercase",
        color: slate[4],
        marginBottom: spacing.xs,
      }}
    >
      {children}
    </div>
  );
}

function IconButton({
  children,
  ariaLabel,
  onClick,
}: {
  children: React.ReactNode;
  ariaLabel: string;
  onClick: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      style={{ border: "none", background: "none", cursor: "pointer", color: slate[4], display: "flex" }}
    >
      {children}
    </button>
  );
}

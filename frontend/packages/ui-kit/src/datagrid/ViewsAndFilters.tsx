import { useState } from "react";
import { Popover } from "@mantine/core";
import { Check, ChevronDown, Plus, Star, Trash2 } from "lucide-react";
import { purple, radius, slate, spacing, typography, white } from "../tokens";
import { FilterBuilder, type FilterBuilderLabels, type FilterBuilderProps } from "./FilterBuilder";
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
  /** The grid's own narrowing of stored predicates (ADR-102: what it reads
   * from a link or a saved view — `offeredPredicates` over its filter scope,
   * after any legacy mapping). Given, an applied view whose filters it narrows
   * carries the unsaved-changes dot and no tick (KAN-475); a grid without
   * filters passes none and never shows the dot. */
  narrowPredicates?: (stored: FilterPredicate[]) => FilterPredicate[];

  labels: ViewsAndFiltersLabels;
}

/** Every string the panel shows or announces, its Filter Builder's
 * included, translated by the caller — the kit ships no English of its own
 * (KAN-364), so `tsc` refuses a caller that passes none. */
export interface ViewsAndFiltersLabels {
  viewsHeading: string;
  saveCurrentView: string;
  savePlaceholder: string;
  /** Accessible name of the icon-only button that saves the typed name
   * (KAN-416). */
  confirmSaveView: string;
  manageFiltersHeading: string;
  reset: string;
  /** Accessible names of a saved view's row actions. */
  setDefault: string;
  unsetDefault: string;
  deleteView: string;
  /** Accessible name and tooltip of the unsaved-changes dot (§ Action Bar,
   * Zones: "an unsaved-changes dot when the current state differs from the
   * saved view") — shown when the applied view's filters are not all
   * applied by this grid (KAN-475). */
  viewChanged: string;
  filters: FilterBuilderLabels;
}

/** True when the grid's own narrowing drops part of a saved view's filters
 * (ADR-102: an unappliable one is ignored quietly) — the grid then shows
 * less than the view, so the control must not present the view as applied
 * (KAN-475). */
export function viewIsNarrowed(view: SavedView, narrow: (stored: FilterPredicate[]) => FilterPredicate[]): boolean {
  const filters = view.snapshot.filters ?? [];
  return filters.length > 0 && narrow(filters).length < filters.length;
}

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
  narrowPredicates,
  labels,
}: ViewsAndFiltersProps) {
  const L = labels;
  const [opened, setOpened] = useState(false);
  // The applied view is the one the trigger names (the list ticks it by
  // name too). Narrowed, it carries the unsaved-changes dot and no tick: the
  // grid is no longer showing that view as saved.
  const appliedView = views.find((view) => view.name === currentViewName);
  const narrowed = appliedView !== undefined && narrowPredicates !== undefined && viewIsNarrowed(appliedView, narrowPredicates);
  const isTicked = (view: SavedView) => view.name === currentViewName && !narrowed;
  const [savingAs, setSavingAs] = useState(false);
  const [newViewName, setNewViewName] = useState("");

  // KAN-1 — this must call through to a real persistence path, never just
  // close the popover and toast. The interactive prototype's own
  // equivalent handler (src/14-page.js) was a documented stub; every
  // caller here wires `onSaveCurrentAsView` to `useSavedViews().saveView`,
  // which persists via `usePersistedPreference` (a real, debounced
  // `PUT /v1/me/preferences/views:<gridKey>`) — confirmed against the
  // actual build, not the prototype, when this comment was added.
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
          {narrowed && (
            <span
              role="img"
              aria-label={L.viewChanged}
              title={L.viewChanged}
              style={{ flexShrink: 0, width: 8, height: 8, borderRadius: radius.full, backgroundColor: purple[6] }}
            />
          )}
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
        {/* KAN-2 — this "Views" section (heading, list, "save as…") must
            render unconditionally, never gated on `views.length`. The
            prototype's own equivalent (src/14-page.js) hid the whole
            section — including the save affordance — whenever a screen
            shipped no preset views, which inverted ADR-058's own point:
            saving the current state is how a view comes into existence,
            so the user with no views yet is exactly the one who most
            needs this control visible. Do not reintroduce that gate. */}
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
                  backgroundColor: isTicked(view) ? purple[0] : undefined,
                }}
                onClick={() => onApplyView(view)}
              >
                {isTicked(view) ? <Check size={14} color={purple[6]} /> : <span style={{ width: 14 }} />}
                <span style={{ flex: 1, fontSize: 13, color: slate[7] }}>{view.name}</span>
                <IconButton
                  ariaLabel={view.isDefault ? L.unsetDefault : L.setDefault}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSetDefaultView(view.isDefault ? null : view.id);
                  }}
                >
                  <Star size={14} fill={view.isDefault ? purple[6] : "none"} color={view.isDefault ? purple[6] : slate[4]} />
                </IconButton>
                <IconButton
                  ariaLabel={L.deleteView}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteView(view.id);
                  }}
                >
                  <Trash2 size={14} />
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
              <button
                type="button"
                onClick={commitSave}
                aria-label={L.confirmSaveView}
                style={{ border: "none", background: "none", color: purple[6], fontWeight: 700, cursor: "pointer" }}
              >
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
          <FilterBuilder
            fields={fields}
            predicates={predicates}
            onChange={onPredicatesChange}
            onPreviewCount={onPreviewCount}
            labels={L.filters}
          />
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

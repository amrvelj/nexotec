import { useEffect, useState } from "react";
import { Copy, Pencil, Plus, Trash2 } from "lucide-react";
import { purple, radius, slate, spacing, white } from "../tokens";
import { CONDITIONS_BY_TYPE, describePredicate, type FilterFieldDef, type FilterPredicate } from "./filterPredicate";

export interface FilterBuilderProps {
  /** Derived from the grid's own column defs (§ Action Bar: "the field
   * list is derived from the grid's own columns") — never a separate,
   * hand-maintained field list that can drift from what the grid shows. */
  fields: FilterFieldDef[];
  predicates: FilterPredicate[];
  onChange: (predicates: FilterPredicate[]) => void;
  /** Live match count before committing a predicate. Omit for a screen
   * with no way yet to count matches for an arbitrary predicate — no
   * module ships a generic filter-count endpoint today, only fixed query
   * parameters (e.g. `/customers?lifecycleStatus=...`); this stays an
   * open backend gap, not something faked on the frontend. */
  onPreviewCount?: (predicates: FilterPredicate[]) => Promise<number>;
  labels?: {
    addFilter?: string;
    save?: string;
    cancel?: string;
    matchCount?: (count: number) => string;
  };
}

const DEFAULT_LABELS = {
  addFilter: "+ New filter…",
  save: "Save",
  cancel: "Cancel",
  matchCount: (count: number) => `${count} matching`,
};

function defaultPredicateFor(field: FilterFieldDef): FilterPredicate {
  const condition = CONDITIONS_BY_TYPE[field.type][0].value;
  return {
    id: crypto.randomUUID(),
    fieldId: field.id,
    type: field.type,
    condition,
    value: field.type === "select" ? field.options?.[0]?.value : field.type === "boolean" ? true : "",
    days: field.type === "date" && (condition === "inTheLastDays" || condition === "moreThanDaysAgo") ? 30 : undefined,
  };
}

/**
 * § Action Bar — Filter Builder. "One predicate: field + condition +
 * value." A list of the screen's own custom filters (editable, duplicable,
 * deletable) plus one inline editor row for adding or editing a single
 * predicate at a time — never a free-text expression box.
 */
export function FilterBuilder({ fields, predicates, onChange, onPreviewCount, labels }: FilterBuilderProps) {
  const L = { ...DEFAULT_LABELS, ...labels };
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<FilterPredicate | null>(null);

  const fieldById = new Map(fields.map((f) => [f.id, f]));

  const startAdd = () => {
    const field = fields[0];
    if (!field) return;
    const predicate = defaultPredicateFor(field);
    setDraft(predicate);
    setEditingId(predicate.id); // not yet in `predicates` — a genuinely new id
  };

  const startEdit = (predicate: FilterPredicate) => {
    setDraft({ ...predicate });
    setEditingId(predicate.id);
  };

  const cancelEdit = () => {
    setDraft(null);
    setEditingId(null);
  };

  const commitDraft = () => {
    if (!draft) return;
    const exists = predicates.some((p) => p.id === draft.id);
    onChange(exists ? predicates.map((p) => (p.id === draft.id ? draft : p)) : [...predicates, draft]);
    cancelEdit();
  };

  const duplicate = (predicate: FilterPredicate) => {
    onChange([...predicates, { ...predicate, id: crypto.randomUUID() }]);
  };

  const remove = (id: string) => {
    onChange(predicates.filter((p) => p.id !== id));
    if (editingId === id) cancelEdit();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.xs }}>
      {predicates.map((predicate) => {
        const field = fieldById.get(predicate.fieldId);
        if (!field) return null; // a field the grid no longer has — silently drops, same as a stale column
        if (editingId === predicate.id && draft) {
          return (
            <PredicateEditorRow
              key={predicate.id}
              fields={fields}
              draft={draft}
              onDraftChange={setDraft}
              onSave={commitDraft}
              onCancel={cancelEdit}
              onPreviewCount={onPreviewCount}
              otherPredicates={predicates.filter((p) => p.id !== predicate.id)}
              labels={L}
            />
          );
        }
        return (
          <div
            key={predicate.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: spacing.xs,
              padding: `${spacing.xs} ${spacing.sm}`,
              borderRadius: radius.sm,
              border: `1px solid ${slate[2]}`,
              fontSize: 13,
              color: slate[7],
            }}
          >
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {describePredicate(predicate, field)}
            </span>
            <IconButton ariaLabel="Edit filter" onClick={() => startEdit(predicate)}>
              <Pencil size={13} />
            </IconButton>
            <IconButton ariaLabel="Duplicate filter" onClick={() => duplicate(predicate)}>
              <Copy size={13} />
            </IconButton>
            <IconButton ariaLabel="Delete filter" onClick={() => remove(predicate.id)}>
              <Trash2 size={13} />
            </IconButton>
          </div>
        );
      })}

      {editingId && draft && !predicates.some((p) => p.id === editingId) && (
        <PredicateEditorRow
          fields={fields}
          draft={draft}
          onDraftChange={setDraft}
          onSave={commitDraft}
          onCancel={cancelEdit}
          onPreviewCount={onPreviewCount}
          otherPredicates={predicates}
          labels={L}
        />
      )}

      {!editingId && (
        <button
          type="button"
          onClick={startAdd}
          disabled={fields.length === 0}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            border: "none",
            background: "none",
            cursor: fields.length === 0 ? "default" : "pointer",
            color: purple[6],
            fontWeight: 600,
            fontSize: 13,
            padding: `${spacing.xs} 0`,
          }}
        >
          <Plus size={14} />
          {L.addFilter}
        </button>
      )}
    </div>
  );
}

function PredicateEditorRow({
  fields,
  draft,
  onDraftChange,
  onSave,
  onCancel,
  onPreviewCount,
  otherPredicates,
  labels,
}: {
  fields: FilterFieldDef[];
  draft: FilterPredicate;
  onDraftChange: (draft: FilterPredicate) => void;
  onSave: () => void;
  onCancel: () => void;
  onPreviewCount?: (predicates: FilterPredicate[]) => Promise<number>;
  otherPredicates: FilterPredicate[];
  labels: Required<Pick<NonNullable<FilterBuilderProps["labels"]>, "save" | "cancel" | "matchCount">>;
}) {
  const [count, setCount] = useState<number | null>(null);
  const field = fields.find((f) => f.id === draft.fieldId);

  useEffect(() => {
    if (!onPreviewCount) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      onPreviewCount([...otherPredicates, draft]).then((n) => {
        if (!cancelled) setCount(n);
      });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.fieldId, draft.condition, draft.value, draft.days]);

  const changeField = (fieldId: string) => {
    const newField = fields.find((f) => f.id === fieldId);
    if (!newField) return;
    const condition = CONDITIONS_BY_TYPE[newField.type][0].value;
    onDraftChange({
      ...draft,
      fieldId,
      type: newField.type,
      condition,
      value: newField.type === "select" ? newField.options?.[0]?.value : newField.type === "boolean" ? true : "",
      days: newField.type === "date" ? 30 : undefined,
    });
  };

  if (!field) return null;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: spacing.xs,
        padding: spacing.sm,
        borderRadius: radius.sm,
        border: `1px solid ${purple[3]}`,
        backgroundColor: purple[0],
      }}
    >
      <select value={draft.fieldId} onChange={(e) => changeField(e.target.value)} style={selectStyle}>
        {fields.map((f) => (
          <option key={f.id} value={f.id}>
            {f.label}
          </option>
        ))}
      </select>

      <select
        value={draft.condition}
        onChange={(e) => onDraftChange({ ...draft, condition: e.target.value })}
        style={selectStyle}
      >
        {CONDITIONS_BY_TYPE[field.type].map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>

      <ValueInput field={field} draft={draft} onDraftChange={onDraftChange} />

      {onPreviewCount && (
        <span style={{ fontSize: 12, color: slate[5], fontStyle: count === null ? "italic" : undefined }}>
          {count === null ? "…" : labels.matchCount(count)}
        </span>
      )}

      <div style={{ display: "flex", gap: spacing.xs, marginLeft: "auto" }}>
        <button type="button" onClick={onCancel} style={ghostButtonStyle}>
          {labels.cancel}
        </button>
        <button type="button" onClick={onSave} style={{ ...ghostButtonStyle, color: purple[7], fontWeight: 700 }}>
          {labels.save}
        </button>
      </div>
    </div>
  );
}

function ValueInput({
  field,
  draft,
  onDraftChange,
}: {
  field: FilterFieldDef;
  draft: FilterPredicate;
  onDraftChange: (draft: FilterPredicate) => void;
}) {
  if (field.type === "select") {
    return (
      <select value={String(draft.value ?? "")} onChange={(e) => onDraftChange({ ...draft, value: e.target.value })} style={selectStyle}>
        {(field.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "boolean") {
    return null; // the condition itself ("is") plus the field label already say everything — see describePredicate
  }
  if (field.type === "number") {
    return (
      <input
        type="number"
        value={typeof draft.value === "number" ? draft.value : ""}
        onChange={(e) => onDraftChange({ ...draft, value: e.target.valueAsNumber })}
        style={inputStyle}
      />
    );
  }
  if (field.type === "date") {
    if (draft.condition === "inTheLastDays" || draft.condition === "moreThanDaysAgo") {
      return (
        <input
          type="number"
          min={1}
          value={draft.days ?? 30}
          onChange={(e) => onDraftChange({ ...draft, days: Number(e.target.value) })}
          style={{ ...inputStyle, width: 60 }}
        />
      );
    }
    return null; // today/thisWeek/thisMonth/thisYear carry no free value
  }
  return (
    <input
      type="text"
      value={typeof draft.value === "string" ? draft.value : ""}
      onChange={(e) => onDraftChange({ ...draft, value: e.target.value })}
      style={inputStyle}
    />
  );
}

function IconButton({ children, ariaLabel, onClick }: { children: React.ReactNode; ariaLabel: string; onClick: () => void }) {
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

const selectStyle: React.CSSProperties = {
  border: `1px solid ${slate[3]}`,
  borderRadius: radius.sm,
  padding: "4px 6px",
  fontSize: 13,
  background: white,
};

const inputStyle: React.CSSProperties = {
  border: `1px solid ${slate[3]}`,
  borderRadius: radius.sm,
  padding: "4px 6px",
  fontSize: 13,
  width: 120,
};

const ghostButtonStyle: React.CSSProperties = {
  border: "none",
  background: "none",
  cursor: "pointer",
  fontSize: 13,
  color: slate[6],
  padding: "4px 8px",
};

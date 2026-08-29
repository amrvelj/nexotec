import { useEffect, useRef, useState, type ReactNode } from "react";
import { Plus, Search } from "lucide-react";
import { purple, radius, slate, spacing } from "../tokens";

export interface PickerRow {
  id: string;
  /** Rendered first, in mono — a customer number, a plate, a VIN. An
   * exact match here ranks above a fuzzy name match, since someone
   * reading a number off a document is not guessing (§ Component
   * Contracts — The picker). */
  identifier?: string;
  label: string;
  sublabel?: string;
}

export interface PickerProps {
  /** "One component, callers supply the rows" — the picker never fetches
   * or filters on its own; the caller re-renders `rows` as the query
   * changes, keeping identifier-ranking logic (exact VIN/plate/number
   * first) with whoever knows the domain. */
  rows: PickerRow[];
  query: string;
  onQueryChange: (query: string) => void;
  onSelect: (row: PickerRow) => void;
  placeholder?: string;
  loading?: boolean;
  /** Every picker has a create path that opens the SAME dialog the
   * master-data module uses — never a thinner copy. Omit only when there
   * is genuinely nothing to create (e.g. picking from a fixed list). */
  onCreateNew?: () => void;
  createLabel?: string;
  emptyLabel?: string;
  autoFocus?: boolean;
}

/**
 * § Component Contracts — The picker. "Which customer?", "which
 * vehicle?" and "which trade-in?" are the same gesture: type a few
 * characters, read a short ranked list, recognise the row, pick it.
 * Nothing is preselected — a form that opens with a value already filled
 * in teaches the user to stop reading that field.
 */
export function Picker({
  rows,
  query,
  onQueryChange,
  onSelect,
  placeholder,
  loading,
  onCreateNew,
  createLabel,
  emptyLabel = "No matches",
  autoFocus = true,
}: PickerProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setActiveIndex(0);
  }, [rows]);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[activeIndex];
      if (row) onSelect(row);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.sm }}>
      <div style={{ position: "relative" }}>
        <Search
          size={16}
          color={slate[4]}
          style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)" }}
          aria-hidden="true"
        />
        <input
          ref={inputRef}
          type="text"
          value={query}
          placeholder={placeholder}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={onKeyDown}
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: `${spacing.sm} ${spacing.sm} ${spacing.sm} 34px`,
            borderRadius: radius.sm,
            border: `1px solid ${slate[2]}`,
            fontSize: 14,
          }}
        />
      </div>

      <div
        role="listbox"
        style={{
          maxHeight: 280,
          overflowY: "auto",
          borderRadius: radius.sm,
          border: rows.length > 0 ? `1px solid ${slate[2]}` : "none",
        }}
      >
        {loading && <div style={{ padding: spacing.md, color: slate[5], fontSize: 13 }}>…</div>}
        {!loading && rows.length === 0 && (
          <div style={{ padding: spacing.md, color: slate[4], fontSize: 13, fontStyle: "italic" }}>{emptyLabel}</div>
        )}
        {!loading &&
          rows.map((row, index) => (
            <PickerRowView
              key={row.id}
              row={row}
              active={index === activeIndex}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => onSelect(row)}
            />
          ))}
      </div>

      {onCreateNew && (
        <button
          type="button"
          onClick={onCreateNew}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            border: "none",
            background: "none",
            cursor: "pointer",
            padding: `${spacing.sm} 0`,
            color: purple[6],
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          <Plus size={14} />
          {createLabel}
        </button>
      )}
    </div>
  );
}

function PickerRowView({
  row,
  active,
  onMouseEnter,
  onClick,
}: {
  row: PickerRow;
  active: boolean;
  onMouseEnter: () => void;
  onClick: () => void;
}): ReactNode {
  return (
    <div
      role="option"
      aria-selected={active}
      onMouseEnter={onMouseEnter}
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: spacing.sm,
        padding: `${spacing.sm} ${spacing.md}`,
        cursor: "pointer",
        backgroundColor: active ? purple[0] : undefined,
      }}
    >
      {row.identifier && (
        <span style={{ fontFamily: "monospace", fontSize: 12, color: slate[6], flexShrink: 0 }}>{row.identifier}</span>
      )}
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: slate[9], overflow: "hidden", textOverflow: "ellipsis" }}>
          {row.label}
        </div>
        {row.sublabel && <div style={{ fontSize: 12, color: slate[5] }}>{row.sublabel}</div>}
      </div>
    </div>
  );
}

import { useRef, useState, type ReactNode } from "react";
import { Pencil } from "lucide-react";
import { TextInput } from "@mantine/core";
import { purple, radius, semantic, slate } from "../tokens";

export interface EditorRenderProps {
  value: string;
  onChange: (next: string) => void;
  autoFocus: boolean;
  onKeyDown: (e: React.KeyboardEvent) => void;
  onBlur: () => void;
  /** Commits an explicit value immediately — for editors like Select
   * where "the user picked an option" IS the confirm action, not
   * something that should wait for a separate Enter/blur. Takes the
   * value directly rather than reading state, since a same-tick
   * onChange(v) + onCommit() pair would otherwise close over the draft
   * value from before the update (React state updates aren't
   * synchronous). */
  onCommit: (value: string) => void;
}

export interface InlineEditFieldProps {
  /** Formatted display value shown outside edit mode. */
  value: string;
  isEmpty?: boolean;
  emptyLabel?: string;
  /** Raw value seeded into the editor; defaults to `value`. */
  editValue?: string;
  /** Persists the new value — throw to reject the edit. Given the actual
   * PATCH/version/If-Match details are entity-specific, this is the
   * caller's job; this component only owns the show/edit toggle and the
   * optimistic-then-rollback UI around it. */
  onSave: (raw: string) => Promise<void>;
  renderEditor?: (props: EditorRenderProps) => ReactNode;
  disabled?: boolean;
  /** True when the thrown error was a version conflict (409) — swaps the
   * generic error message for "someone else changed this" plus a reload
   * affordance, per FR-05 / "optimistic update with rollback on 409,
   * which offers a reload". */
  isConflict?: (err: unknown) => boolean;
  onReload?: () => void;
}

/**
 * § UI/UX Core Principles — Detail Screens: "Editing is inline, not
 * modal. Hover a value to reveal a subtle purple.0 edit affordance;
 * click to edit in place; Enter saves, Esc cancels. Optimistic update
 * with rollback on 409, which offers a reload (FR-05)."
 *
 * Blur also saves (not just Enter) — inline editors that discard on
 * blur read as data loss the moment focus moves; Esc is the explicit,
 * discoverable way to discard instead.
 */
export function InlineEditField({
  value,
  isEmpty,
  emptyLabel = "Not set",
  editValue,
  onSave,
  renderEditor,
  disabled,
  isConflict,
  onReload,
}: InlineEditFieldProps) {
  const [editing, setEditing] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ message: string; conflict: boolean } | null>(null);
  const committingRef = useRef(false);

  const startEdit = () => {
    if (disabled) return;
    setDraft(editValue ?? value);
    setError(null);
    setEditing(true);
  };

  const cancel = () => {
    committingRef.current = true;
    setEditing(false);
  };

  const commit = async (overrideValue?: string) => {
    if (committingRef.current) return;
    committingRef.current = true;
    const raw = overrideValue ?? draft;
    setSaving(true);
    try {
      await onSave(raw);
      setEditing(false);
      setError(null);
    } catch (err) {
      const conflict = isConflict?.(err) ?? false;
      setError({
        message: conflict ? "Someone else changed this in the meantime." : err instanceof Error ? err.message : "Failed to save.",
        conflict,
      });
      // Stay in edit mode so the value (and the error) remain visible —
      // silently reverting would throw away what the user just typed.
      setEditing(true);
    } finally {
      setSaving(false);
      committingRef.current = false;
    }
  };

  if (editing) {
    const editorProps: EditorRenderProps = {
      value: draft,
      onChange: setDraft,
      autoFocus: true,
      onKeyDown: (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          void commit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          cancel();
        }
      },
      onBlur: () => {
        // A blur caused by commit()'s own setEditing(false) shouldn't
        // re-trigger a save.
        if (!committingRef.current) void commit();
      },
      onCommit: (v) => void commit(v),
    };
    return (
      <div style={{ textAlign: "left" }}>
        {renderEditor ? (
          renderEditor(editorProps)
        ) : (
          <TextInput
            size="xs"
            value={editorProps.value}
            onChange={(e) => editorProps.onChange(e.currentTarget.value)}
            onKeyDown={editorProps.onKeyDown}
            onBlur={editorProps.onBlur}
            autoFocus
            disabled={saving}
          />
        )}
        {error && (
          <div style={{ fontSize: 12, color: semantic.destructive.text, marginTop: 4, display: "flex", gap: 6, alignItems: "center" }}>
            <span>{error.message}</span>
            {error.conflict && onReload && (
              <button
                type="button"
                onClick={onReload}
                style={{ border: "none", background: "none", color: purple[6], cursor: "pointer", fontWeight: 600, padding: 0 }}
              >
                Reload
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <span
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onClick={startEdit}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        cursor: disabled ? undefined : "pointer",
        padding: "2px 6px",
        margin: "-2px -6px",
        borderRadius: radius.sm,
        backgroundColor: hovering && !disabled ? purple[0] : undefined,
      }}
    >
      <span style={{ fontStyle: isEmpty ? "italic" : undefined, color: isEmpty ? slate[3] : undefined }}>{isEmpty ? emptyLabel : value}</span>
      {hovering && !disabled && <Pencil size={12} color={purple[6]} aria-hidden="true" />}
    </span>
  );
}

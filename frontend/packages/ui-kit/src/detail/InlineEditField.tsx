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

interface InlineEditFieldBaseProps {
  /** Formatted display value shown outside edit mode. */
  value: string;
  isEmpty?: boolean;
  /** Shown when `isEmpty` — translated by the caller (KAN-422). */
  emptyLabel: string;
  /** Raw value seeded into the editor; defaults to `value`. */
  editValue?: string;
  /** Persists the new value — throw to reject the edit. Given the actual
   * PATCH/version/If-Match details are entity-specific, this is the
   * caller's job; this component only owns the show/edit toggle and the
   * optimistic-then-rollback UI around it. */
  onSave: (raw: string) => Promise<void>;
  /** KAN-473 — the sentence shown for whatever `onSave` threw, in the UI
   * language. The kit never shows a caught error's own `message` (an
   * ApiError's is the API's English text, for logs) and ships no wording of
   * its own (`.claude/rules/ui-i18n.md`), so every caller says it — the app's
   * `useInlineEditRefusal()`. */
  describeError: (err: unknown) => string;
  renderEditor?: (props: EditorRenderProps) => ReactNode;
  disabled?: boolean;
  /** True when the thrown error was a version conflict (a stale If-Match
   * 409) — adds a reload affordance beside the error, per FR-05 /
   * "optimistic update with rollback on 409, which offers a reload". */
  isConflict?: (err: unknown) => boolean;
}

/** A reload affordance comes with its label, so no caller can render it in
 * English by omission. */
type InlineEditFieldReloadProps =
  | { onReload: () => void; reloadLabel: string }
  | { onReload?: undefined; reloadLabel?: undefined };

export type InlineEditFieldProps = InlineEditFieldBaseProps & InlineEditFieldReloadProps;

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
  emptyLabel,
  editValue,
  onSave,
  describeError,
  renderEditor,
  disabled,
  isConflict,
  onReload,
  reloadLabel,
}: InlineEditFieldProps) {
  const [editing, setEditing] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<{ text: string; conflict: boolean } | null>(null);
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
      setError({ text: describeError(err), conflict: isConflict?.(err) ?? false });
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
      <div
        style={{ textAlign: "left" }}
        // Belt-and-suspenders on top of editorProps.onKeyDown/onBlur:
        // custom renderEditor implementations (e.g. a compound editor with
        // more than one input) can easily forget to wire those through to
        // every inner control. Enter/Escape/blur bubble up to this
        // wrapper regardless, so commit/cancel always fires. contains()
        // guards against firing a spurious commit when focus just moves
        // between two inputs inside the same custom editor.
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        }}
        onBlur={(e) => {
          if (committingRef.current) return;
          const next = e.relatedTarget as Node | null;
          if (next && e.currentTarget.contains(next)) return;
          void commit();
        }}
      >
        {renderEditor ? (
          renderEditor(editorProps)
        ) : (
          <TextInput
            size="xs"
            value={editorProps.value}
            onChange={(e) => editorProps.onChange(e.currentTarget.value)}
            autoFocus
            disabled={saving}
          />
        )}
        {error && (
          <div style={{ fontSize: 12, color: semantic.destructive.text, marginTop: 4, display: "flex", gap: 6, alignItems: "center" }}>
            <span>{error.text}</span>
            {error.conflict && onReload && (
              <button
                type="button"
                onClick={onReload}
                style={{ border: "none", background: "none", color: purple[6], cursor: "pointer", fontWeight: 600, padding: 0, whiteSpace: "nowrap" }}
              >
                {reloadLabel}
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
      {hovering && !disabled && <Pencil size={14} color={purple[6]} aria-hidden="true" />}
    </span>
  );
}

import { useRef, useState, type ReactNode } from "react";
import { Pencil } from "lucide-react";
import { Popover, TextInput } from "@mantine/core";
import { useInsideGridCell } from "../datagrid/cellContainment";
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
  // KAN-710 — inside a DataGrid cell the line below the editor is clipped.
  const insideGridCell = useInsideGridCell();
  const popoverRef = useRef<HTMLDivElement>(null);
  const reloadRef = useRef<HTMLButtonElement>(null);

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
    const editor = renderEditor ? (
      renderEditor(editorProps)
    ) : (
      <TextInput
        size="xs"
        value={editorProps.value}
        onChange={(e) => editorProps.onChange(e.currentTarget.value)}
        autoFocus
        disabled={saving}
      />
    );
    const refusal = error && (
      <div
        role="alert"
        style={{
          fontSize: 12,
          color: semantic.destructive.text,
          marginTop: insideGridCell ? 0 : 4,
          display: "flex",
          gap: 6,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <span>{error.text}</span>
        {error.conflict && onReload && (
          <button
            ref={reloadRef}
            type="button"
            // In the popover the button sits outside the editor's wrapper (a
            // portal): pressing it must not blur the editor, whose blur
            // would save the refused value again before the reload.
            onMouseDown={(e) => e.preventDefault()}
            onClick={onReload}
            style={{ border: "none", background: "none", color: purple[6], cursor: "pointer", fontWeight: 600, padding: 0, whiteSpace: "nowrap" }}
          >
            {reloadLabel}
          </button>
        )}
      </div>
    );
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
        //
        // KAN-710: React events bubble out of a portal to this wrapper, so
        // the grid-cell popover counts as part of the editor: Enter on its
        // «reload» is the button's own, focus moving into it is no blur,
        // and Tab from the editor reaches it (the portal sits at the end of
        // the page, outside the tab order beside the editor).
        onKeyDown={(e) => {
          const inPopover = popoverRef.current?.contains(e.target as Node) ?? false;
          if (e.key === "Enter") {
            if (inPopover) return;
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          } else if (e.key === "Tab" && !e.shiftKey && !inPopover && insideGridCell && reloadRef.current) {
            e.preventDefault();
            reloadRef.current.focus();
          }
        }}
        onBlur={(e) => {
          if (committingRef.current) return;
          const next = e.relatedTarget as Node | null;
          if (next && (e.currentTarget.contains(next) || popoverRef.current?.contains(next))) return;
          void commit();
        }}
      >
        {insideGridCell ? (
          // KAN-710 (ADR-164): a grid cell clips everything below its one
          // line, so there the refusal and «reload» open in a popover
          // anchored to the editor, in a portal above the grid. Opened only
          // while there is a refusal; focus stays in the editor.
          <Popover
            opened={error != null}
            position="bottom-start"
            withinPortal
            withArrow
            shadow="md"
            trapFocus={false}
            returnFocus={false}
            closeOnEscape={false}
            closeOnClickOutside={false}
          >
            <Popover.Target>
              <div>{editor}</div>
            </Popover.Target>
            <Popover.Dropdown ref={popoverRef} p={6} style={{ maxWidth: 360, whiteSpace: "normal" }}>
              {error && refusal}
            </Popover.Dropdown>
          </Popover>
        ) : (
          <>
            {editor}
            {error && refusal}
          </>
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

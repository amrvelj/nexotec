import { useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Star } from "lucide-react";
import { purple, radius, semantic, slate, spacing, white } from "../tokens";
import { FieldLabel, VISUALLY_HIDDEN } from "./FieldHint";
import { RowMenu } from "./RowMenu";

export interface RepeatableRowValue {
  id: string;
  type: string;
  value: string;
  label?: string | null;
  isPrimary: boolean;
  /** Set once the row is closed — "no longer valid." Kept, never cleared:
   * a closed row stays readable, it just stops being current. */
  validTo?: string | null;
  doNotUse?: boolean;
  doNotUseReason?: string | null;
  consentGranted: boolean;
  /** FR-23 §1 — WHAT the consent covers. null == marketing on a row that
   * predates the scope. */
  consentScope?: string | null;
  consentSource?: string | null;
  consentTimestamp?: string | null;
}

export interface RepeatableRowTypeOption {
  value: string;
  label: string;
}

export type RepeatableRowPatch = Partial<
  Pick<
    RepeatableRowValue,
    | "type"
    | "value"
    | "label"
    | "isPrimary"
    | "validTo"
    | "doNotUse"
    | "doNotUseReason"
    | "consentGranted"
    | "consentScope"
    | "consentSource"
    | "consentTimestamp"
  >
>;

export interface RepeatableRowGroupProps {
  /** `"create"` hides former (closed/doNotUse) rows entirely — a record
   * being created has no history yet — where `"detail"` collapses them
   * behind the "former ..." toggle instead of hiding them outright. */
  mode: "create" | "detail";
  label: string;
  /** KAN-460 — the value column's name ("Nummer", "Adresse"): its header,
   * and the accessible name `renderValueEditor` is handed for its input. */
  valueLabel: string;
  /** "+ Add <thing>" — labelled with what it adds, never a bare "+ Add". */
  addLabel: string;
  formerLabel: string;
  typeOptions: RepeatableRowTypeOption[];
  /** The type a freshly added row starts as. */
  defaultType: string;
  rows: RepeatableRowValue[];
  /** `accessibleName` is `valueLabel`: the editor names its input with it
   * (the column header says it once for every row, KAN-460). */
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean, accessibleName: string) => ReactNode;
  /** The narrowest the value editor may get, in px (KAN-267). A group too
   * narrow for the column template at that width stacks each row's cells,
   * each with its own visible label, instead of squeezing the editor. A
   * composite editor (a phone's country and number) passes what it needs
   * to stay readable; groups meant to line up pass the same width. */
  valueEditorMinWidth?: number;
  onCreate: (draft: { type: string; value: string }) => Promise<void>;
  onUpdate: (id: string, patch: RepeatableRowPatch) => Promise<void>;
  /** Real deletion — only ever offered for a row created THIS session
   * (added after the component first mounted, never persisted as history
   * before now). Every other row's menu offers "no longer valid"/"does
   * not work" instead, both implemented as `onUpdate` calls. */
  onDelete: (id: string) => Promise<void>;
  labels: RepeatableRowGroupLabels;
  /** FR-23 §1 — when consent is granted the advisor picks WHAT it covers
   * (scope) and HOW it was given (source). Pass translated option lists to
   * turn the consent checkbox into scope + source selectors; omit them and
   * the checkbox stays a bare granted toggle (older callers). */
  consentScopeOptions?: RepeatableRowTypeOption[];
  consentSourceOptions?: RepeatableRowTypeOption[];
  /** The scope a freshly granted consent starts as — `marketing` unless
   * overridden, which is the meaning a bare `granted` has always had. */
  defaultConsentScope?: string;
  /** Turns a thrown `onCreate`/`onUpdate`/`onDelete` error into row-level
   * text in the UI language (the app's `apiRefusalText`). Without it a failed
   * write shows `labels.genericError` — never the error's own `message`,
   * which for an API refusal is English (KAN-473). */
  describeError?: (err: unknown) => string;
}

/** Every string the group shows or announces, translated by the caller —
 * the kit ships no English of its own (KAN-364), so `tsc` refuses a caller
 * that passes none. */
export interface RepeatableRowGroupLabels {
  primary: string;
  noLongerValid: string;
  doesNotWork: string;
  doesNotWorkReasonPlaceholder: string;
  delete: string;
  consent: string;
  /** The consent selectors' captions, shown only with `consentScopeOptions`. */
  consentScope: string;
  consentSource: string;
  save: string;
  cancel: string;
  confirm: string;
  none: string;
  /** Shown for a failed write when no `describeError` is given. */
  genericError: string;
  /** KAN-460 — the column names, said once per group in its header row and
   * as each cell's accessible name; the label and consent columns carry a
   * hint (an info icon beside the name, KAN-443). */
  typeColumn: string;
  labelColumn: string;
  labelHint: string;
  consentHint: string;
  /** The hint icon's accessible name ("Hinweis"). */
  hintIcon: string;
  /** KAN-291 — the row menu trigger's accessible name, given the group's
   * `label` ("Phone" → "Phone row actions"). */
  rowActions: (label: string) => string;
}

// Wide enough for an e-mail address or a name in the kit's 13px type.
const DEFAULT_VALUE_EDITOR_MIN_WIDTH = 200;

// KAN-460 — one column template for every row of every group, so a phone
// row and an e-mail row line up: star · type · value · label · consent ·
// menu. Only the value column grows; the others are fixed, the star's kept
// even when a group shows none. The header row uses the same template, and
// each row names it in `data-column-template` (jsdom drops the `min()`).
const STAR_COLUMN = 18;
const TYPE_COLUMN = 130;
const LABEL_COLUMN = 140;
const CONSENT_COLUMN = 104;
const MENU_COLUMN = 28;
const COLUMN_GAP = 4; // --sp-xs

function rowColumnTemplate(valueMinWidth: number): string {
  return `${STAR_COLUMN}px ${TYPE_COLUMN}px minmax(min(${valueMinWidth}px, 100%), 1fr) ${LABEL_COLUMN}px ${CONSENT_COLUMN}px ${MENU_COLUMN}px`;
}

/** The narrowest group the template fits at its value minimum. */
function templateMinWidth(valueMinWidth: number): number {
  return STAR_COLUMN + TYPE_COLUMN + valueMinWidth + LABEL_COLUMN + CONSENT_COLUMN + MENU_COLUMN + 5 * COLUMN_GAP;
}

/** The group's own width, or null until it is known (jsdom has no layout:
 * it reports 0, read as unknown — the template then renders). */
function useGroupWidth(ref: React.RefObject<HTMLDivElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      const measured = element.getBoundingClientRect().width;
      setWidth(measured > 0 ? measured : null);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function isFormer(row: RepeatableRowValue): boolean {
  return Boolean(row.doNotUse) || Boolean(row.validTo && new Date(row.validTo).getTime() <= Date.now());
}

/**
 * § ADR-067 — The Repeatable Row Group. "A field that's really a list of
 * rows is not three numbered inputs." One row per value; the model, the
 * API and the six read-model projections are WP-3 — this package builds
 * the control on top of them, the same component in a create dialog and
 * on a detail screen.
 */
export function RepeatableRowGroup({
  mode,
  label,
  valueLabel,
  addLabel,
  formerLabel,
  typeOptions,
  defaultType,
  rows,
  renderValueEditor,
  valueEditorMinWidth = DEFAULT_VALUE_EDITOR_MIN_WIDTH,
  onCreate,
  onUpdate,
  onDelete,
  labels,
  consentScopeOptions,
  consentSourceOptions,
  defaultConsentScope = "marketing",
  describeError,
}: RepeatableRowGroupProps) {
  const L = labels;
  const hintIdBase = useId();
  const groupRef = useRef<HTMLDivElement>(null);
  const groupWidth = useGroupWidth(groupRef);
  // Too narrow for the template: each row stacks its cells, each with its
  // own label (KAN-267's wrap, KAN-460's labels kept).
  const stacked = groupWidth !== null && groupWidth < templateMinWidth(valueEditorMinWidth);
  const template = rowColumnTemplate(valueEditorMinWidth);
  const labelHintId = `${hintIdBase}-label-hint`;
  const consentHintId = `${hintIdBase}-consent-hint`;
  const hintLabels = { icon: L.hintIcon };
  const labelHeading = <FieldLabel label={L.labelColumn} hint={L.labelHint} labels={hintLabels} />;
  const consentHeading = <FieldLabel label={L.consent} hint={L.consentHint} labels={hintLabels} />;
  const cell = (part: string, caption: ReactNode, content: ReactNode, style?: CSSProperties) => (
    <div data-row-part={part} style={{ minWidth: 0, ...(stacked ? stackedCellStyle(part, valueEditorMinWidth) : {}), ...style }}>
      {stacked && caption !== null && <div style={captionStyle}>{caption}</div>}
      {content}
    </div>
  );
  const errorText = (err: unknown): string => describeError?.(err) ?? L.genericError;

  // "Real deletion only for same-session mistakes" — a row present when
  // this component FIRST mounted is existing history and can only ever be
  // closed, never deleted; anything added after that is fair game.
  const sessionStartIds = useRef<Set<string>>(new Set(rows.map((r) => r.id)));
  const isSessionDraft = (id: string) => !sessionStartIds.current.has(id);

  const [showFormer, setShowFormer] = useState(false);
  const [adding, setAdding] = useState(false);
  const [markingDoesNotWorkId, setMarkingDoesNotWorkId] = useState<string | null>(null);
  const [doesNotWorkReason, setDoesNotWorkReason] = useState("");
  const [doesNotWorkError, setDoesNotWorkError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  const setRowError = (id: string, message: string) => setRowErrors((prev) => ({ ...prev, [id]: message }));
  const clearRowError = (id: string) => setRowErrors((prev) => ({ ...prev, [id]: "" }));

  const current = rows.filter((r) => !isFormer(r));
  const former = rows.filter(isFormer);

  // "Only the primary of each type is visible until a second is added" —
  // group current rows by type; a lone row of its type needs no star (it
  // is trivially the only candidate), so the star only ever appears once
  // there is something to compare it against.
  const byType = useMemo(() => {
    const groups = new Map<string, RepeatableRowValue[]>();
    for (const row of current) {
      const group = groups.get(row.type) ?? [];
      group.push(row);
      groups.set(row.type, group);
    }
    return groups;
  }, [current]);

  const setPrimary = async (row: RepeatableRowValue) => {
    if (row.isPrimary) return;
    clearRowError(row.id);
    try {
      // A single PATCH — the service layer's own `_unset_other_primaries`
      // already unmarks every sibling of the same type transactionally.
      // Looping over siblings and firing one PATCH per row here would be
      // both redundant and the opposite of "the same interaction": several
      // separate HTTP requests that could partially fail, instead of the
      // one atomic transaction the backend already provides.
      await onUpdate(row.id, { isPrimary: true });
    } catch (err) {
      setRowError(row.id, errorText(err));
    }
  };

  const toggleConsent = async (row: RepeatableRowValue) => {
    const granted = !row.consentGranted;
    clearRowError(row.id);
    try {
      await onUpdate(
        row.id,
        granted
          // FR-23 §1 — a grant must name its scope; default to `marketing`
          // (its historical meaning), the advisor refines with the scope
          // selector. `consentTimestamp` is stamped server-side now.
          ? { consentGranted: true, consentScope: row.consentScope ?? defaultConsentScope }
          : { consentGranted: false },
      );
    } catch (err) {
      setRowError(row.id, errorText(err));
    }
  };

  const changeConsentField = async (row: RepeatableRowValue, patch: RepeatableRowPatch) => {
    clearRowError(row.id);
    try {
      await onUpdate(row.id, patch);
    } catch (err) {
      setRowError(row.id, errorText(err));
    }
  };

  const commitDoesNotWork = async () => {
    if (!markingDoesNotWorkId) return;
    setDoesNotWorkError(null);
    try {
      await onUpdate(markingDoesNotWorkId, { doNotUse: true, doNotUseReason: doesNotWorkReason });
      setMarkingDoesNotWorkId(null);
      setDoesNotWorkReason("");
    } catch (err) {
      setDoesNotWorkError(errorText(err));
    }
  };

  const closeNoLongerValid = async (row: RepeatableRowValue) => {
    clearRowError(row.id);
    try {
      await onUpdate(row.id, { validTo: new Date().toISOString() });
    } catch (err) {
      setRowError(row.id, errorText(err));
    }
  };

  const deleteDraft = async (row: RepeatableRowValue) => {
    clearRowError(row.id);
    try {
      await onDelete(row.id);
    } catch (err) {
      setRowError(row.id, errorText(err));
    }
  };

  const renderRow = (row: RepeatableRowValue) => {
    const group = byType.get(row.type) ?? [];
    const showStar = group.length > 1;
    const draft = isSessionDraft(row.id);

    if (markingDoesNotWorkId === row.id) {
      return (
        <div key={row.id} style={rowContainerStyle}>
          <input
            autoFocus
            value={doesNotWorkReason}
            onChange={(e) => setDoesNotWorkReason(e.target.value)}
            placeholder={L.doesNotWorkReasonPlaceholder}
            style={{ flex: 1, border: `1px solid ${slate[3]}`, borderRadius: radius.sm, padding: "4px 8px", fontSize: 13 }}
          />
          <button type="button" onClick={() => void commitDoesNotWork()} style={confirmButtonStyle}>
            {L.confirm}
          </button>
          <button
            type="button"
            onClick={() => {
              setMarkingDoesNotWorkId(null);
              setDoesNotWorkError(null);
            }}
            style={cancelButtonStyle}
          >
            {L.cancel}
          </button>
          {doesNotWorkError && <span style={{ fontSize: 11, color: semantic.destructive.text }}>{doesNotWorkError}</span>}
        </div>
      );
    }

    const typeLabel = typeOptions.find((o) => o.value === row.type)?.label ?? row.type;

    return (
      <div key={row.id}>
        <div
          data-row-layout={stacked ? "stacked" : "template"}
          data-column-template={stacked ? undefined : template}
          style={stacked ? stackedRowStyle : templateRowStyle(template)}
        >
          {/* The template keeps the star's column; a stacked row only a star. */}
          {(!stacked || showStar) && cell("star", null, showStar && (
            <button
              type="button"
              onClick={() => void setPrimary(row)}
              aria-label={row.isPrimary ? `${L.primary} (${typeLabel})` : `${L.primary}?`}
              aria-pressed={row.isPrimary}
              style={{ border: "none", background: "none", cursor: "pointer", display: "flex", padding: 0 }}
            >
              <Star size={14} fill={row.isPrimary ? purple[6] : "none"} color={row.isPrimary ? purple[6] : slate[3]} />
            </button>
          ))}

          {cell(
            "type",
            L.typeColumn,
            <select
              aria-label={L.typeColumn}
              value={row.type}
              onChange={(e) => void onUpdate(row.id, { type: e.target.value })}
              style={typeSelectStyle}
            >
              {typeOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>,
          )}

          {cell("value", valueLabel, renderValueEditor(row.value, (v) => void onUpdate(row.id, { value: v }), false, valueLabel))}

          {cell(
            "label",
            labelHeading,
            <input
              aria-label={L.labelColumn}
              aria-describedby={labelHintId}
              value={row.label ?? ""}
              onChange={(e) => void onUpdate(row.id, { label: e.target.value || null })}
              style={labelInputStyle}
            />,
          )}

          {cell(
            "consent",
            consentHeading,
            <input
              type="checkbox"
              aria-label={L.consent}
              aria-describedby={consentHintId}
              checked={row.consentGranted}
              onChange={() => void toggleConsent(row)}
            />,
            stacked ? undefined : { display: "flex", justifyContent: "center" },
          )}

          {cell(
            "menu",
            null,
            <RowMenu
              ariaLabel={L.rowActions(label)}
              groups={{
                edit: [
                  { label: L.noLongerValid, icon: null, onClick: () => void closeNoLongerValid(row) },
                  { label: L.doesNotWork, icon: null, onClick: () => setMarkingDoesNotWorkId(row.id) },
                ],
                destructive: draft ? [{ label: L.delete, icon: null, onClick: () => void deleteDraft(row) }] : [],
              }}
            />,
          )}
        </div>
        {/* FR-23 §1 — a granted consent shows (and edits) WHAT it covers
            and HOW it was given, on a second line so the main row stays
            legible. */}
        {row.consentGranted && consentScopeOptions && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: spacing.xs, marginLeft: stacked ? 0 : STAR_COLUMN + COLUMN_GAP, marginTop: 2, fontSize: 11, color: slate[5] }}>
            <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {L.consentScope}
              <select
                value={row.consentScope ?? "marketing"}
                onChange={(e) => void changeConsentField(row, { consentScope: e.target.value })}
                style={{ border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "2px 4px", fontSize: 11, background: white }}
              >
                {consentScopeOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            {consentSourceOptions && (
              <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
                {L.consentSource}
                <select
                  value={row.consentSource ?? ""}
                  onChange={(e) => void changeConsentField(row, { consentSource: e.target.value || null })}
                  style={{ border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "2px 4px", fontSize: 11, background: white }}
                >
                  <option value="">—</option>
                  {consentSourceOptions.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}
        {rowErrors[row.id] && <div style={{ fontSize: 11, color: semantic.destructive.text, marginLeft: stacked ? 0 : STAR_COLUMN + COLUMN_GAP }}>{rowErrors[row.id]}</div>}
      </div>
    );
  };

  const renderFormerRow = (row: RepeatableRowValue) => {
    const reason = row.doNotUse ? row.doNotUseReason : null;
    return (
      <div key={row.id} style={{ ...rowContainerStyle, opacity: 0.55 }}>
        <span style={{ flex: 1, textDecoration: "line-through", fontSize: 13, color: slate[6] }}>{row.value}</span>
        {reason && <span style={{ fontSize: 11, color: semantic.destructive.text }}>{reason}</span>}
      </div>
    );
  };

  return (
    <div ref={groupRef} style={{ display: "flex", flexDirection: "column", gap: spacing.xs }}>
      {/* The hints the label and consent inputs of every row point at. */}
      <span id={labelHintId} style={VISUALLY_HIDDEN}>
        {L.labelHint}
      </span>
      <span id={consentHintId} style={VISUALLY_HIDDEN}>
        {L.consentHint}
      </span>

      {current.length === 0 && !adding && <div style={{ fontSize: 13, color: slate[4], fontStyle: "italic" }}>{L.none}</div>}

      {/* KAN-460 — the columns named once per group, on the rows' own
          template; a stacked group labels each cell instead. */}
      {!stacked && (current.length > 0 || adding) && (
        <div data-row-part="header" data-column-template={template} style={{ ...templateRowStyle(template), ...captionStyle }}>
          <div />
          <div>{L.typeColumn}</div>
          <div>{valueLabel}</div>
          <div>{labelHeading}</div>
          <div style={{ display: "flex", justifyContent: "center" }}>{consentHeading}</div>
          <div />
        </div>
      )}

      {current.map(renderRow)}

      {adding ? (
        <AddRow
          typeOptions={typeOptions}
          defaultType={defaultType}
          renderValueEditor={renderValueEditor}
          valueLabel={valueLabel}
          typeColumnLabel={L.typeColumn}
          template={stacked ? null : template}
          valueEditorMinWidth={valueEditorMinWidth}
          saveLabel={L.save}
          cancelLabel={L.cancel}
          errorText={errorText}
          onCreate={onCreate}
          onClose={() => setAdding(false)}
        />
      ) : (
        <button type="button" onClick={() => setAdding(true)} style={addButtonStyle}>
          + {addLabel}
        </button>
      )}

      {/* § ADR-067 — "collapses behind a toggle on detail, hidden entirely
          in create." A create dialog has no history to show at all. */}
      {mode === "detail" && former.length > 0 && (
        <div style={{ marginTop: spacing.xs }}>
          <button type="button" onClick={() => setShowFormer((s) => !s)} style={formerToggleStyle}>
            {showFormer ? "▾" : "▸"} {formerLabel} ({former.length})
          </button>
          {showFormer && <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>{former.map(renderFormerRow)}</div>}
        </div>
      )}
    </div>
  );
}

interface AddRowProps {
  typeOptions: RepeatableRowTypeOption[];
  defaultType: string;
  renderValueEditor: RepeatableRowGroupProps["renderValueEditor"];
  valueLabel: string;
  typeColumnLabel: string;
  /** The group's column template, or null when the group is stacked. */
  template: string | null;
  valueEditorMinWidth: number;
  saveLabel: string;
  cancelLabel: string;
  errorText: (err: unknown) => string;
  onCreate: RepeatableRowGroupProps["onCreate"];
  onClose: () => void;
}

/**
 * The add row, with the draft as its own state (KAN-167): a keystroke in it
 * re-renders this row only, never the group's existing rows — each of which
 * may carry a heavy value editor (a phone's Mantine Select). Mounted fresh
 * for every "+ Add", so a new draft starts at the default type, empty.
 */
function AddRow({
  typeOptions,
  defaultType,
  renderValueEditor,
  valueLabel,
  typeColumnLabel,
  template,
  valueEditorMinWidth,
  saveLabel,
  cancelLabel,
  errorText,
  onCreate,
  onClose,
}: AddRowProps) {
  const [draftType, setDraftType] = useState(defaultType);
  const [draftValue, setDraftValue] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [addSaving, setAddSaving] = useState(false);

  const commitAdd = async () => {
    setAddSaving(true);
    setAddError(null);
    try {
      await onCreate({ type: draftType, value: draftValue });
    } catch (err) {
      setAddError(errorText(err));
      setAddSaving(false);
      return;
    }
    onClose();
  };

  return (
    <div
      data-row-layout={template ? "template" : "stacked"}
      data-column-template={template ?? undefined}
      style={template ? templateRowStyle(template) : stackedRowStyle}
    >
      {template && <div />}
      <div data-row-part="type" style={template ? undefined : stackedCellStyle("type", valueEditorMinWidth)}>
        {/* Stacked, the add row's cells keep their labels too: no header names them (KAN-460). */}
        {!template && <div style={captionStyle}>{typeColumnLabel}</div>}
        <select aria-label={typeColumnLabel} value={draftType} onChange={(e) => setDraftType(e.target.value)} style={typeSelectStyle}>
          {typeOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div data-row-part="value" style={template ? { minWidth: 0 } : stackedCellStyle("value", valueEditorMinWidth)}>
        {!template && <div style={captionStyle}>{valueLabel}</div>}
        {renderValueEditor(draftValue, setDraftValue, true, valueLabel)}
      </div>
      <div data-row-part="trailing" style={{ ...trailingClusterStyle, ...(template ? { gridColumn: "4 / span 3" } : {}) }}>
        <button type="button" onClick={() => void commitAdd()} disabled={!draftValue || addSaving} style={confirmButtonStyle}>
          {saveLabel}
        </button>
        <button type="button" onClick={onClose} disabled={addSaving} style={cancelButtonStyle}>
          {cancelLabel}
        </button>
      </div>
      {addError && <span style={{ fontSize: 11, color: semantic.destructive.text }}>{addError}</span>}
    </div>
  );
}

const rowContainerStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: spacing.xs,
};

function templateRowStyle(template: string): React.CSSProperties {
  return { display: "grid", gridTemplateColumns: template, alignItems: "center", columnGap: spacing.xs };
}

// A group too narrow for the template (KAN-267): the cells wrap, the value
// editor at its minimum (capped at the row's width, so a very narrow card
// still shows it whole on its own line), never squeezed to a few pixels.
const stackedRowStyle: React.CSSProperties = { ...rowContainerStyle, alignItems: "flex-end", rowGap: spacing.sm };

function stackedCellStyle(part: string, valueMinWidth: number): React.CSSProperties {
  if (part === "value") return { flex: `1 1 ${valueMinWidth}px`, minWidth: `min(${valueMinWidth}px, 100%)` };
  if (part === "type") return { flex: `0 0 ${TYPE_COLUMN}px` };
  if (part === "label") return { flex: `0 0 ${LABEL_COLUMN}px` };
  return { flex: "0 0 auto" };
}

const captionStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: slate[5], marginBottom: 2 };

const typeSelectStyle: React.CSSProperties = {
  width: "100%",
  border: `1px solid ${slate[2]}`,
  borderRadius: radius.sm,
  padding: "4px 6px",
  fontSize: 13,
  background: white,
};

const labelInputStyle: React.CSSProperties = {
  width: "100%",
  minWidth: 0,
  boxSizing: "border-box",
  border: `1px solid ${slate[2]}`,
  borderRadius: radius.sm,
  padding: "4px 6px",
  fontSize: 13,
  color: slate[6],
};

const trailingClusterStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: spacing.xs,
  marginLeft: "auto",
  flexShrink: 0,
};

const addButtonStyle: React.CSSProperties = {
  alignSelf: "flex-start",
  border: "none",
  background: "none",
  cursor: "pointer",
  color: purple[6],
  fontWeight: 600,
  fontSize: 13,
  padding: 0,
};

const confirmButtonStyle: React.CSSProperties = {
  border: "none",
  borderRadius: radius.sm,
  padding: "4px 10px",
  fontSize: 12,
  fontWeight: 600,
  color: white,
  backgroundColor: purple[6],
  cursor: "pointer",
};

const cancelButtonStyle: React.CSSProperties = {
  border: "none",
  background: "none",
  cursor: "pointer",
  fontSize: 12,
  fontWeight: 600,
  color: slate[6],
};

const formerToggleStyle: React.CSSProperties = {
  border: "none",
  background: "none",
  cursor: "pointer",
  fontSize: 12,
  color: slate[5],
  padding: 0,
};

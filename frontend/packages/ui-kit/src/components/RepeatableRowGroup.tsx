import { useMemo, useRef, useState, type ReactNode } from "react";
import { Star } from "lucide-react";
import { purple, radius, semantic, slate, spacing, white } from "../tokens";
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
    "type" | "value" | "label" | "isPrimary" | "validTo" | "doNotUse" | "doNotUseReason" | "consentGranted" | "consentSource" | "consentTimestamp"
  >
>;

export interface RepeatableRowGroupProps {
  /** `"create"` hides former (closed/doNotUse) rows entirely — a record
   * being created has no history yet — where `"detail"` collapses them
   * behind the "former ..." toggle instead of hiding them outright. */
  mode: "create" | "detail";
  label: string;
  /** "+ Add <thing>" — labelled with what it adds, never a bare "+ Add". */
  addLabel: string;
  formerLabel: string;
  typeOptions: RepeatableRowTypeOption[];
  /** The type a freshly added row starts as. */
  defaultType: string;
  rows: RepeatableRowValue[];
  renderValueEditor: (value: string, onChange: (v: string) => void, autoFocus: boolean) => ReactNode;
  onCreate: (draft: { type: string; value: string }) => Promise<void>;
  onUpdate: (id: string, patch: RepeatableRowPatch) => Promise<void>;
  /** Real deletion — only ever offered for a row created THIS session
   * (added after the component first mounted, never persisted as history
   * before now). Every other row's menu offers "no longer valid"/"does
   * not work" instead, both implemented as `onUpdate` calls. */
  onDelete: (id: string) => Promise<void>;
  labels?: {
    primary?: string;
    noLongerValid?: string;
    doesNotWork?: string;
    doesNotWorkReasonPlaceholder?: string;
    delete?: string;
    consent?: string;
    save?: string;
    cancel?: string;
    confirm?: string;
    none?: string;
    genericError?: string;
    labelFieldPlaceholder?: string;
  };
  /** Recorded on the row when its consent checkbox is checked — this app
   * has no self-service customer portal, so consent is always captured by
   * whoever is operating this screen. */
  consentSourceValue?: string;
  /** Turns a thrown `onCreate`/`onUpdate`/`onDelete` error into row-level
   * text — e.g. unwrapping an `ApiError`'s own message. Defaults to the
   * error's own `message`, which is not translated. */
  describeError?: (err: unknown) => string;
}

const DEFAULT_LABELS = {
  primary: "Primary",
  noLongerValid: "No longer valid",
  doesNotWork: "Does not work",
  doesNotWorkReasonPlaceholder: "Reason (e.g. bounced, disconnected)",
  delete: "Delete",
  consent: "Consent",
  save: "Save",
  cancel: "Cancel",
  confirm: "Confirm",
  none: "None recorded",
  genericError: "Something went wrong.",
  labelFieldPlaceholder: "Label",
};

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
  addLabel,
  formerLabel,
  typeOptions,
  defaultType,
  rows,
  renderValueEditor,
  onCreate,
  onUpdate,
  onDelete,
  labels,
  consentSourceValue = "advisor",
  describeError,
}: RepeatableRowGroupProps) {
  const L = { ...DEFAULT_LABELS, ...labels };
  const errorText = (err: unknown): string => describeError?.(err) ?? (err instanceof Error ? err.message : L.genericError);

  // "Real deletion only for same-session mistakes" — a row present when
  // this component FIRST mounted is existing history and can only ever be
  // closed, never deleted; anything added after that is fair game.
  const sessionStartIds = useRef<Set<string>>(new Set(rows.map((r) => r.id)));
  const isSessionDraft = (id: string) => !sessionStartIds.current.has(id);

  const [showFormer, setShowFormer] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draftType, setDraftType] = useState(defaultType);
  const [draftValue, setDraftValue] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [addSaving, setAddSaving] = useState(false);
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

  const startAdd = () => {
    setDraftType(defaultType);
    setDraftValue("");
    setAddError(null);
    setAdding(true);
  };

  const commitAdd = async () => {
    setAddSaving(true);
    setAddError(null);
    try {
      await onCreate({ type: draftType, value: draftValue });
      setAdding(false);
      setDraftValue("");
    } catch (err) {
      setAddError(errorText(err));
    } finally {
      setAddSaving(false);
    }
  };

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
      await onUpdate(row.id, {
        consentGranted: granted,
        consentSource: granted ? consentSourceValue : row.consentSource,
        // `consentTimestamp` is deliberately NOT sent — neither
        // CustomerPhoneUpdate nor CustomerEmailUpdate accepts it
        // (app/customer/schemas/customer.py), and nothing in the service
        // layer stamps it on a consent change either, so a value sent here
        // would be silently dropped by Pydantic's default `extra="ignore"`
        // rather than actually persisted. A real, open backend gap —
        // flagged here rather than pretended-away on the frontend, which
        // is out of this package's scope to fix (WP-6c owns presentation).
      });
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
        <div style={rowContainerStyle}>
          {showStar && (
            <button
              type="button"
              onClick={() => void setPrimary(row)}
              aria-label={row.isPrimary ? `${L.primary} (${typeLabel})` : `${L.primary}?`}
              aria-pressed={row.isPrimary}
              style={{ border: "none", background: "none", cursor: "pointer", display: "flex", padding: 0 }}
            >
              <Star size={14} fill={row.isPrimary ? purple[6] : "none"} color={row.isPrimary ? purple[6] : slate[3]} />
            </button>
          )}

          <select
            value={row.type}
            onChange={(e) => void onUpdate(row.id, { type: e.target.value })}
            style={{ flex: "0 0 130px", border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "4px 6px", fontSize: 13, background: white }}
          >
            {typeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <div style={{ flex: 1, minWidth: 0 }}>{renderValueEditor(row.value, (v) => void onUpdate(row.id, { value: v }), false)}</div>

          <input
            value={row.label ?? ""}
            onChange={(e) => void onUpdate(row.id, { label: e.target.value || null })}
            placeholder={L.labelFieldPlaceholder}
            style={{ flex: "0 0 90px", border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "4px 6px", fontSize: 12, color: slate[6] }}
          />

          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: slate[5], flexShrink: 0 }}>
            <input type="checkbox" checked={row.consentGranted} onChange={() => void toggleConsent(row)} />
            {L.consent}
          </label>

          <RowMenu
            ariaLabel={`${label} row actions`}
            groups={{
              edit: [
                { label: L.noLongerValid, icon: null, onClick: () => void closeNoLongerValid(row) },
                { label: L.doesNotWork, icon: null, onClick: () => setMarkingDoesNotWorkId(row.id) },
              ],
              destructive: draft ? [{ label: L.delete, icon: null, onClick: () => void deleteDraft(row) }] : [],
            }}
          />
        </div>
        {rowErrors[row.id] && <div style={{ fontSize: 11, color: semantic.destructive.text, marginLeft: showStar ? 19 : 0 }}>{rowErrors[row.id]}</div>}
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
    <div style={{ display: "flex", flexDirection: "column", gap: spacing.xs }}>
      {current.length === 0 && !adding && <div style={{ fontSize: 13, color: slate[4], fontStyle: "italic" }}>{L.none}</div>}

      {current.map(renderRow)}

      {adding ? (
        <div style={rowContainerStyle}>
          <select
            value={draftType}
            onChange={(e) => setDraftType(e.target.value)}
            style={{ flex: "0 0 130px", border: `1px solid ${slate[2]}`, borderRadius: radius.sm, padding: "4px 6px", fontSize: 13, background: white }}
          >
            {typeOptions.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <div style={{ flex: 1 }}>{renderValueEditor(draftValue, setDraftValue, true)}</div>
          <button type="button" onClick={() => void commitAdd()} disabled={!draftValue || addSaving} style={confirmButtonStyle}>
            {L.save}
          </button>
          <button type="button" onClick={() => setAdding(false)} disabled={addSaving} style={cancelButtonStyle}>
            {L.cancel}
          </button>
          {addError && <span style={{ fontSize: 11, color: semantic.destructive.text }}>{addError}</span>}
        </div>
      ) : (
        <button type="button" onClick={startAdd} style={addButtonStyle}>
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

const rowContainerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: spacing.xs,
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

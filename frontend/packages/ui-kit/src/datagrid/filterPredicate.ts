/**
 * § Action Bar — Filter Builder. "One predicate: field + condition +
 * value, never a compound expression typed into a box." Pure model,
 * deliberately framework-free (same "verify by query" reasoning as
 * columnLayout.ts) — `FilterBuilder.tsx` is the UI on top of this.
 */

export type FilterFieldType = "text" | "select" | "date" | "boolean" | "number";

export interface FilterFieldOption {
  value: string;
  label: string;
}

export interface FilterFieldDef {
  /** The column id this predicate filters — the SAME id the column
   * registry uses (§ ADR-058: filters, sorts and columns are one set), and
   * the stable key a stored predicate's `fieldId` carries. The API
   * parameter it maps to is `param`, which may differ. */
  id: string;
  label: string;
  type: FilterFieldType;
  /** Required for `type: "select"` — every legal value, translated. */
  options?: FilterFieldOption[];
  /** API query parameter this field maps to. Defaults to `id`. Kept
   * separate so a param rename does not invalidate a saved view whose
   * predicate stored the column id. */
  param?: string;
  /** Restrict the offered conditions to this subset of
   * `CONDITIONS_BY_TYPE[type]`. Use it so the builder only ever offers a
   * predicate the backing endpoint can actually honour. Omit for the full
   * set. */
  conditions?: string[];
}

export type TextCondition = "contains" | "equals" | "notEquals";
export type SelectCondition = "is" | "isNot";
export type NumberCondition = "equals" | "greaterThan" | "lessThan";
export type BooleanCondition = "is";
/**
 * Every date condition is relative — "date conditions are RELATIVE never
 * absolute" (§ Action Bar). There is no calendar-day condition anywhere in
 * this union on purpose; a saved "changed in the last 30 days" filter
 * means the same thing every time it's opened, where a saved "changed
 * since 12.03.2026" would silently go stale.
 */
export type DateCondition = "today" | "thisWeek" | "thisMonth" | "thisYear" | "inTheLastDays" | "moreThanDaysAgo";

export type ConditionForType<T extends FilterFieldType> = T extends "text"
  ? TextCondition
  : T extends "select"
    ? SelectCondition
    : T extends "number"
      ? NumberCondition
      : T extends "boolean"
        ? BooleanCondition
        : DateCondition;

export interface FilterPredicate {
  id: string;
  fieldId: string;
  type: FilterFieldType;
  condition: string;
  /** Absent for the boolean "is true" shorthand and for date conditions
   * that carry no free value of their own (`today`/`thisWeek`/…). */
  value?: string | number | boolean;
  /** Only `inTheLastDays` / `moreThanDaysAgo` use this. */
  days?: number;
}

/** Every condition value a predicate can store. The values are stable —
 * saved views keep them and grids send them as query parameters — so a
 * label never changes one. */
export type FilterCondition = TextCondition | SelectCondition | NumberCondition | BooleanCondition | DateCondition;

export const CONDITIONS_BY_TYPE: Record<FilterFieldType, FilterCondition[]> = {
  text: ["contains", "equals", "notEquals"],
  select: ["is", "isNot"],
  number: ["equals", "greaterThan", "lessThan"],
  boolean: ["is"],
  date: ["today", "thisWeek", "thisMonth", "thisYear", "inTheLastDays", "moreThanDaysAgo"],
};

/** What a saved filter's text is built from: the field's label, the
 * value as shown (a select option's label, not its stored value), and the
 * day count of the two N-days conditions. */
export interface PredicateTextParts {
  field: string;
  value: string;
  days: number;
}

/** Every string the kit needs to name a condition or write a predicate
 * out, translated by the caller (KAN-415). A saved filter's text is one
 * template per condition rather than field + condition + value glued
 * together, so each language keeps its own word order. */
export interface FilterConditionLabels {
  /** The condition dropdown's options, keyed by condition value. */
  conditions: Record<FilterCondition, string>;
  /** A saved filter's text, keyed by condition value — "Canton : contient
   * ZH", "Modifié : au cours des 30 derniers jours". A boolean field uses
   * `booleanTrue`/`booleanFalse` instead. */
  predicateText: Record<FilterCondition, (parts: PredicateTextParts) => string>;
  booleanTrue: (field: string) => string;
  booleanFalse: (field: string) => string;
}

/**
 * The conditions a field actually offers — the type's full set
 * (`CONDITIONS_BY_TYPE`) narrowed to `field.conditions` when it declares
 * one. A field narrows its set so the builder never offers a predicate the
 * backing endpoint cannot honour (which would otherwise be accepted and
 * then silently dropped).
 */
export function conditionsForField(field: FilterFieldDef): FilterCondition[] {
  const all = CONDITIONS_BY_TYPE[field.type];
  if (!field.conditions) return all;
  const allowed = new Set(field.conditions);
  const narrowed = all.filter((c) => allowed.has(c));
  return narrowed.length > 0 ? narrowed : all;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function startOfDay(d: Date): Date {
  const copy = new Date(d);
  copy.setHours(0, 0, 0, 0);
  return copy;
}

function addDays(d: Date, days: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + days);
  return copy;
}

export interface ResolvedDateRange {
  /** Inclusive ISO (`YYYY-MM-DD`) bounds. `null` means unbounded on that
   * side (`moreThanDaysAgo` has no lower bound). */
  from: string | null;
  to: string | null;
}

/**
 * Turns a relative date condition into concrete bounds AT THE MOMENT it's
 * evaluated — the entire reason this exists instead of a stored absolute
 * date. `now` is a parameter, never read internally, so this stays a pure
 * function a test can pin to an exact instant.
 */
export function resolveRelativeDateRange(condition: DateCondition, days: number | undefined, now: Date): ResolvedDateRange {
  const today = startOfDay(now);

  switch (condition) {
    case "today":
      return { from: toIsoDate(today), to: toIsoDate(today) };
    case "thisWeek": {
      // ISO week: Monday start, matching the Swiss convention used
      // elsewhere in this app (dd.MM.yyyy, not the US Sunday-start week).
      const dayOfWeek = today.getDay() === 0 ? 7 : today.getDay(); // Sun=0 -> 7
      const monday = addDays(today, 1 - dayOfWeek);
      const sunday = addDays(monday, 6);
      return { from: toIsoDate(monday), to: toIsoDate(sunday) };
    }
    case "thisMonth": {
      const first = new Date(today.getFullYear(), today.getMonth(), 1);
      const last = new Date(today.getFullYear(), today.getMonth() + 1, 0);
      return { from: toIsoDate(first), to: toIsoDate(last) };
    }
    case "thisYear": {
      const first = new Date(today.getFullYear(), 0, 1);
      const last = new Date(today.getFullYear(), 11, 31);
      return { from: toIsoDate(first), to: toIsoDate(last) };
    }
    case "inTheLastDays": {
      const n = days ?? 0;
      return { from: toIsoDate(addDays(today, -n)), to: toIsoDate(today) };
    }
    case "moreThanDaysAgo": {
      const n = days ?? 0;
      return { from: null, to: toIsoDate(addDays(today, -n - 1)) };
    }
  }
}

/** A saved filter's text for the "Manage filters" list — "Canton: ZH",
 * "Changed: in the last 30 days" — in the caller's language. A condition
 * the field's type does not know (a stale saved view) reads as the field's
 * label alone rather than as a raw condition value. */
export function describePredicate(predicate: FilterPredicate, field: FilterFieldDef, labels: FilterConditionLabels): string {
  if (predicate.type === "boolean") {
    return predicate.value ? labels.booleanTrue(field.label) : labels.booleanFalse(field.label);
  }
  const condition = CONDITIONS_BY_TYPE[predicate.type].find((c) => c === predicate.condition);
  if (!condition) return field.label;
  const value =
    predicate.type === "select"
      ? (field.options?.find((o) => o.value === predicate.value)?.label ?? String(predicate.value ?? ""))
      : String(predicate.value ?? "");
  return labels.predicateText[condition]({ field: field.label, value, days: predicate.days ?? 0 });
}

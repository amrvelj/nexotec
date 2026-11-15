/**
 * § Action Bar — Filter Builder. "One predicate: field + condition +
 * value, never a compound expression typed into a box." Pure model,
 * deliberately framework-free (same "verify by query" reasoning as
 * columnLayout.ts) — `FilterBuilder.tsx` is the UI on top of this.
 */

import { formatCurrencyChf, formatNumber } from "../format/swissNumber";

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
  /** How a `type: "number"` field's value reads in a saved filter's text
   * (KAN-421): `"grouped"` (the default) through `formatNumber` — 40'000,
   * as the grid shows it (KAN-160); `"chf"` through `formatCurrencyChf` —
   * CHF 40'000.00 (ADR-076); `"ungrouped"` as entered — a year, 2024. The
   * definition says which; it is never guessed from the label. */
  numberFormat?: "grouped" | "chf" | "ungrouped";
}

export type TextCondition = "contains" | "equals" | "notEquals";
export type SelectCondition = "is" | "isNot";
/** `atLeast`/`atMost` are the inclusive bounds (≥ / ≤) an endpoint's
 * `…Min`/`…Max` range parameters apply; `greaterThan`/`lessThan` are strict.
 * A field offers only the ones its endpoint honours exactly (KAN-458). */
export type NumberCondition = "equals" | "greaterThan" | "lessThan" | "atLeast" | "atMost" | "between";
export type BooleanCondition = "is";
/**
 * The relative date conditions — a saved "changed in the last 30 days"
 * filter means the same thing every time it's opened.
 */
export type RelativeDateCondition = "today" | "thisWeek" | "thisMonth" | "thisYear" | "inTheLastDays" | "moreThanDaysAgo";
/**
 * KAN-461 (Anto, 2026-11-15): «am», «vor», «nach» a calendar day, beside the
 * relative ones — offered only by a field that lists them (a grid on the
 * list-query layer), never by default. Their value is an ISO date
 * (`YYYY-MM-DD`), a calendar day in the session's zone.
 */
export type AbsoluteDateCondition = "on" | "before" | "after";
export type DateCondition = RelativeDateCondition | AbsoluteDateCondition;

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
  /** Only `between` uses this: the inclusive upper bound; `value` is the
   * lower one (KAN-461 — a field holds one filter, ranges included). */
  valueTo?: number;
}

/** Every condition value a predicate can store. The values are stable —
 * saved views keep them and grids send them as query parameters — so a
 * label never changes one. */
export type FilterCondition = TextCondition | SelectCondition | NumberCondition | BooleanCondition | DateCondition;

/** What a field offers when it lists no conditions of its own. */
export const CONDITIONS_BY_TYPE: Record<FilterFieldType, FilterCondition[]> = {
  text: ["contains", "equals", "notEquals"],
  select: ["is", "isNot"],
  number: ["equals", "greaterThan", "lessThan", "atLeast", "atMost"],
  boolean: ["is"],
  date: ["today", "thisWeek", "thisMonth", "thisYear", "inTheLastDays", "moreThanDaysAgo"],
};

/** Every condition the kit can build, by type — a field reaches the ones
 * beyond `CONDITIONS_BY_TYPE` (`between`, `on`/`before`/`after`, KAN-461)
 * only by listing them in `conditions`, so a grid whose endpoint does not
 * apply them never offers them. */
export const ALL_CONDITIONS_BY_TYPE: Record<FilterFieldType, FilterCondition[]> = {
  text: ["contains", "equals", "notEquals"],
  select: ["is", "isNot"],
  number: ["equals", "greaterThan", "lessThan", "atLeast", "atMost", "between"],
  boolean: ["is"],
  date: ["today", "thisWeek", "thisMonth", "thisYear", "inTheLastDays", "moreThanDaysAgo", "on", "before", "after"],
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
  /** A calendar day (`YYYY-MM-DD`) as the user reads one — the app's
   * `formatDateOnly` (KAN-461's «am», «vor», «nach»). Omitted: dd.MM.yyyy. */
  formatDay?: (isoDay: string) => string;
}

/**
 * The conditions a field actually offers — the type's full set
 * (`CONDITIONS_BY_TYPE`) narrowed to `field.conditions` when it declares
 * one. A field narrows its set so the builder never offers a predicate the
 * backing endpoint cannot honour (which would otherwise be accepted and
 * then silently dropped).
 */
export function conditionsForField(field: FilterFieldDef): FilterCondition[] {
  if (!field.conditions) return CONDITIONS_BY_TYPE[field.type];
  const allowed = new Set(field.conditions);
  const narrowed = ALL_CONDITIONS_BY_TYPE[field.type].filter((c) => allowed.has(c));
  return narrowed.length > 0 ? narrowed : CONDITIONS_BY_TYPE[field.type];
}

/** The three date conditions that name a calendar day (KAN-461). */
export function takesCalendarDay(condition: FilterCondition | string): boolean {
  return condition === "on" || condition === "before" || condition === "after";
}

/** A calendar day as the endpoints take it: `YYYY-MM-DD`, a real date. */
export function isValidIsoDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

/** A `between` range the endpoint accepts: two numbers, the lower first. */
export function isValidRange(value: unknown, valueTo: unknown): boolean {
  return (
    typeof value === "number" && Number.isFinite(value) && typeof valueTo === "number" && Number.isFinite(valueTo) && value <= valueTo
  );
}

/** Whether a predicate carries every value its condition needs — what the
 * builder commits and what a grid sends (a link or a saved view may hold
 * one that does not). */
export function hasCompleteValue(predicate: FilterPredicate): boolean {
  if (predicate.type === "date" && takesDayCount(predicate.condition)) return isValidDayCount(predicate.days);
  if (predicate.type === "date" && takesCalendarDay(predicate.condition)) return isValidIsoDay(predicate.value);
  if (predicate.type === "number" && predicate.condition === "between") return isValidRange(predicate.value, predicate.valueTo);
  return true;
}

/** The day counts an N-days condition takes — the list endpoints' own
 * bounds (`ge=0`, `le=36500`, about a hundred years;
 * `app/customer/api/customers.py`). 0 is today's day boundary. */
export const MIN_FILTER_DAYS = 0;
export const MAX_FILTER_DAYS = 36500;

/** The two date conditions that carry a day count. */
export function takesDayCount(condition: FilterCondition | string): boolean {
  return condition === "inTheLastDays" || condition === "moreThanDaysAgo";
}

/** A day count an endpoint accepts: a whole number from `MIN_FILTER_DAYS`
 * to `MAX_FILTER_DAYS`. Anything else (a fraction, a negative number, more
 * than 36500, an empty field) the API refuses with a 422 (KAN-475). */
export function isValidDayCount(days: unknown): days is number {
  return typeof days === "number" && Number.isInteger(days) && days >= MIN_FILTER_DAYS && days <= MAX_FILTER_DAYS;
}

/**
 * The predicates a grid can honour: those whose field it offers, with the
 * field's type, under a condition the field offers — and only the first on
 * each field: a field holds one filter (Anto, 2026-10-26; ADR-102). A
 * predicate reaches a grid from its URL or a saved view, not only from the
 * builder (which replaces a field's filter) — one the grid does not apply
 * must neither be sent nor shown as an active filter, or the screen claims a
 * result it did not compute (KAN-458). An N-days predicate with a day count
 * the endpoint refuses is one of those (KAN-475): sent, it would show the
 * grid's error state instead of a list.
 */
export function offeredPredicates(predicates: FilterPredicate[], fields: FilterFieldDef[]): FilterPredicate[] {
  const byId = new Map(fields.map((f) => [f.id, f]));
  const taken = new Set<string>();
  return predicates.filter((p) => {
    const field = byId.get(p.fieldId);
    if (field === undefined || field.type !== p.type) return false;
    if (!conditionsForField(field).includes(p.condition as FilterCondition)) return false;
    if (!hasCompleteValue(p)) return false;
    if (taken.has(field.id)) return false;
    taken.add(field.id);
    return true;
  });
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
export function resolveRelativeDateRange(condition: RelativeDateCondition, days: number | undefined, now: Date): ResolvedDateRange {
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
  const condition = ALL_CONDITIONS_BY_TYPE[predicate.type].find((c) => c === predicate.condition);
  if (!condition) return field.label;
  const number = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) ? formatNumberValue(v, field.numberFormat ?? "grouped") : String(v ?? "");
  const value =
    predicate.type === "select"
      ? (field.options?.find((o) => o.value === predicate.value)?.label ?? String(predicate.value ?? ""))
      : predicate.type === "number" && condition === "between"
        ? `${number(predicate.value)} – ${number(predicate.valueTo)}`
        : predicate.type === "number"
          ? number(predicate.value)
          : predicate.type === "date" && isValidIsoDay(predicate.value)
            ? (labels.formatDay ?? formatIsoDay)(predicate.value)
            : String(predicate.value ?? "");
  return labels.predicateText[condition]({ field: field.label, value, days: predicate.days ?? 0 });
}

/** A calendar day as the grid shows one: dd.MM.yyyy (ui-formats). */
function formatIsoDay(day: string): string {
  const [y, m, d] = day.split("-");
  return `${d}.${m}.${y}`;
}

function formatNumberValue(value: number, format: NonNullable<FilterFieldDef["numberFormat"]>): string {
  if (format === "chf") return formatCurrencyChf(value);
  if (format === "ungrouped") return String(value);
  return formatNumber(value);
}

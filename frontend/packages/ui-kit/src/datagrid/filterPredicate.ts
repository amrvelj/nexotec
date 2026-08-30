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
  /** Matches the field's own API filter parameter name — this is what a
   * caller turns a resolved predicate into an actual query with. */
  id: string;
  label: string;
  type: FilterFieldType;
  /** Required for `type: "select"` — every legal value, translated. */
  options?: FilterFieldOption[];
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

export const CONDITIONS_BY_TYPE: Record<FilterFieldType, { value: string; label: string }[]> = {
  text: [
    { value: "contains", label: "contains" },
    { value: "equals", label: "is exactly" },
    { value: "notEquals", label: "is not" },
  ],
  select: [
    { value: "is", label: "is" },
    { value: "isNot", label: "is not" },
  ],
  number: [
    { value: "equals", label: "equals" },
    { value: "greaterThan", label: "greater than" },
    { value: "lessThan", label: "less than" },
  ],
  boolean: [{ value: "is", label: "is" }],
  date: [
    { value: "today", label: "is today" },
    { value: "thisWeek", label: "is this week" },
    { value: "thisMonth", label: "is this month" },
    { value: "thisYear", label: "is this year" },
    { value: "inTheLastDays", label: "is in the last N days" },
    { value: "moreThanDaysAgo", label: "is more than N days ago" },
  ],
};

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

/** A human-readable chip label — "Canton is ZH", "Changed in the last 30
 * days" — for `FilterChips`/the "Manage filters" list. */
export function describePredicate(predicate: FilterPredicate, field: FilterFieldDef): string {
  const conditionLabel =
    CONDITIONS_BY_TYPE[predicate.type].find((c) => c.value === predicate.condition)?.label ?? predicate.condition;

  if (predicate.type === "select" && field.options) {
    const optionLabel = field.options.find((o) => o.value === predicate.value)?.label ?? String(predicate.value);
    return `${field.label} ${conditionLabel} ${optionLabel}`;
  }
  if (predicate.type === "date" && (predicate.condition === "inTheLastDays" || predicate.condition === "moreThanDaysAgo")) {
    return `${field.label} ${conditionLabel.replace("N", String(predicate.days ?? 0))}`;
  }
  if (predicate.type === "date") {
    return `${field.label} ${conditionLabel}`;
  }
  if (predicate.type === "boolean") {
    return predicate.value ? field.label : `Not ${field.label}`;
  }
  return `${field.label} ${conditionLabel} ${predicate.value ?? ""}`.trim();
}

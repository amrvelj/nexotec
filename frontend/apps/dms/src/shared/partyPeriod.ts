/**
 * KAN-644 / KAN-583 (Anto, 2026-11-12): one reading of a vehicle-party row's
 * period, shared by the customer's Vehicles tab and the vehicle's Identity
 * tab — the same test the server's `_holds_now_or_later` applies.
 *
 * - upcoming: its start is still to come (a planned handover). Never shown as
 *   the current holder.
 * - planned, never started: closed at its own start (an empty period) — a
 *   planned entry cancelled before it began.
 * - holds now or later: open, or ending later, and not empty.
 */
interface Period {
  effectiveFrom: string
  effectiveTo?: string | null
}

const at = (iso: string) => new Date(iso).getTime()

export function isPlannedNeverStarted(row: Period): boolean {
  return row.effectiveTo != null && at(row.effectiveTo) === at(row.effectiveFrom)
}

export function isUpcoming(row: Period, now: number = Date.now()): boolean {
  return at(row.effectiveFrom) > now && !isPlannedNeverStarted(row)
}

export function holdsNowOrLater(row: Period, now: number = Date.now()): boolean {
  if (isPlannedNeverStarted(row)) return false
  return row.effectiveTo == null || at(row.effectiveTo) > now
}

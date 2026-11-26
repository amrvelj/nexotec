/**
 * KAN-809 — the planner's time, always Europe/Zurich, whatever the browser's
 * own zone: the API's instants are UTC, a person's working hours and the day
 * the page shows are Zurich wall-clock (planning.md). Intl does the zone
 * arithmetic; no library.
 */

export const PLANNER_ZONE = 'Europe/Zurich'

const PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: PLANNER_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

interface WallClock {
  date: string // YYYY-MM-DD
  time: string // HH:MM
}

/** An instant as Zurich wall-clock date and time. */
export function wallClock(instant: string | Date): WallClock {
  const parts = Object.fromEntries(
    PARTS.formatToParts(typeof instant === 'string' ? new Date(instant) : instant).map((p) => [p.type, p.value]),
  )
  return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` }
}

/** Zurich's offset from UTC at an instant, in minutes. */
function offsetAt(utcMillis: number): number {
  const { date, time } = wallClock(new Date(utcMillis))
  const [y, m, d] = date.split('-').map(Number)
  const [h, mi] = time.split(':').map(Number)
  return (Date.UTC(y, m - 1, d, h, mi) - Math.floor(utcMillis / 60000) * 60000) / 60000
}

/** A Zurich wall-clock date and time as an ISO instant with its offset. In
 * the hour the clock skips, the time is read with the offset before the change. */
export function zurichInstant(date: string, time: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const [h, mi] = time.split(':').map(Number)
  const naive = Date.UTC(y, m - 1, d, h, mi)
  let offset = offsetAt(naive - offsetAt(naive) * 60000)
  if (offsetAt(naive - offset * 60000) !== offset) offset = offsetAt(naive)
  const sign = offset >= 0 ? '+' : '-'
  const abs = Math.abs(offset)
  const hh = String(Math.floor(abs / 60)).padStart(2, '0')
  const mm = String(abs % 60).padStart(2, '0')
  return `${date}T${time}:00${sign}${hh}:${mm}`
}

/** The minute of the shown day an instant falls on: before the day 0, after it 1440+. */
export function minuteOfDay(instant: string, day: string): number {
  const { date, time } = wallClock(instant)
  const [h, mi] = time.split(':').map(Number)
  const minute = h * 60 + mi
  if (date === day) return minute
  return date < day ? -1 : 24 * 60 + minute
}

/** Today in Zurich, as YYYY-MM-DD. */
export function zurichToday(now: Date = new Date()): string {
  return wallClock(now).date
}

/** A YYYY-MM-DD day moved by a number of days. */
export function shiftDay(day: string, days: number): string {
  const [y, m, d] = day.split('-').map(Number)
  const moved = new Date(Date.UTC(y, m - 1, d + days))
  const mm = String(moved.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(moved.getUTCDate()).padStart(2, '0')
  return `${moved.getUTCFullYear()}-${mm}-${dd}`
}

/** ISO weekday of a YYYY-MM-DD day: 1 = Monday … 7 = Sunday (working hours' convention). */
export function isoWeekday(day: string): number {
  const [y, m, d] = day.split('-').map(Number)
  const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return weekday === 0 ? 7 : weekday
}

/** An instant as the planner shows it: «dd.MM.yyyy HH:mm», Zurich time (the
 * Betrieb's day — the same in every UI language, FR-13). */
export function zurichDateTime(instant: string): string {
  const { date, time } = wallClock(instant)
  const [y, m, d] = date.split('-')
  return `${d}.${m}.${y} ${time}`
}

/** «HH:MM» to minutes. */
export function minutesOf(time: string): number {
  const [h, mi] = time.split(':').map(Number)
  return h * 60 + mi
}

/** Minutes to «HH:MM». */
export function clockOf(minute: number): string {
  const m = ((minute % 1440) + 1440) % 1440
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

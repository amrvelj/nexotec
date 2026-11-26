import type { WorkingDay, WorkingHoursUpdate } from '../../api/types'

/**
 * KAN-808 — a person's working week in the planner-resources screen. The API
 * holds one row per worked ISO weekday (1 = Monday … 7 = Sunday) with an
 * optional second interval (after the lunch break); a weekday not sent is not
 * worked. The PUT replaces the whole week under the resource's If-Match.
 * Local wall-clock times to the minute (Europe/Zurich), never instants.
 */
export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const

export interface WorkingDayForm {
  works: boolean
  startsAt: string
  endsAt: string
  /** The second interval, empty when there is none. */
  secondStartsAt: string
  secondEndsAt: string
}

export type WorkingWeekForm = Record<number, WorkingDayForm>

const OFF: WorkingDayForm = { works: false, startsAt: '', endsAt: '', secondStartsAt: '', secondEndsAt: '' }

/** "07:30:00" → "07:30" (the API reads either; the inputs show minutes). */
export const hhmm = (value: string | null | undefined): string => (value ? value.slice(0, 5) : '')

export function weekToForm(days: readonly WorkingDay[]): WorkingWeekForm {
  const week = Object.fromEntries(WEEKDAYS.map((day) => [day, { ...OFF }])) as WorkingWeekForm
  for (const day of days) {
    week[day.weekday] = {
      works: true,
      startsAt: hhmm(day.startsAt),
      endsAt: hhmm(day.endsAt),
      secondStartsAt: hhmm(day.secondStartsAt),
      secondEndsAt: hhmm(day.secondEndsAt),
    }
  }
  return week
}

/** A day's entries as the API takes them, or null when they cannot be true —
 * the same rule as the API's `planning_working_hours_invalid`: each interval
 * ends after it starts, the second starts after the first ends. */
function dayToApi(weekday: number, day: WorkingDayForm): WorkingDay | null {
  if (!day.startsAt || !day.endsAt || day.startsAt >= day.endsAt) return null
  const hasSecond = day.secondStartsAt !== '' || day.secondEndsAt !== ''
  if (!hasSecond) return { weekday, startsAt: day.startsAt, endsAt: day.endsAt, secondStartsAt: null, secondEndsAt: null }
  if (!day.secondStartsAt || !day.secondEndsAt) return null
  if (day.secondStartsAt <= day.endsAt || day.secondStartsAt >= day.secondEndsAt) return null
  return {
    weekday,
    startsAt: day.startsAt,
    endsAt: day.endsAt,
    secondStartsAt: day.secondStartsAt,
    secondEndsAt: day.secondEndsAt,
  }
}

/** The PUT body, or the first weekday whose times cannot be true. */
export function formToWeek(week: WorkingWeekForm): { body: WorkingHoursUpdate } | { invalidWeekday: number } {
  const days: WorkingDay[] = []
  for (const weekday of WEEKDAYS) {
    const day = week[weekday]
    if (!day.works) continue
    const api = dayToApi(weekday, day)
    if (api === null) return { invalidWeekday: weekday }
    days.push(api)
  }
  return { body: { days } }
}

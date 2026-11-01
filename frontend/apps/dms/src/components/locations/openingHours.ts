import type { LocationDepartment, OpeningHoursDay, OpeningHoursRead, OpeningHoursUpdate } from '../../api/types'
import { DEPARTMENTS, WEEKDAYS } from './locationOptions'

/**
 * KAN-204 (D-A-27) — the hours editor's form. One week per department is
 * always held; in the one-set mode (the default) only `sales` is edited and
 * the PUT sends days without a department, which the API stores for all
 * three. Switching "differs per department" on copies the one set into each
 * department, so nothing typed is lost.
 */
export interface DayForm {
  open: boolean
  opensAt: string
  closesAt: string
  /** The second interval (after the lunch break), empty when there is none. */
  secondOpensAt: string
  secondClosesAt: string
}

export type WeekForm = Record<number, DayForm>

export interface HoursForm {
  perDepartment: boolean
  weeks: Record<LocationDepartment, WeekForm>
}

const CLOSED_DAY: DayForm = { open: false, opensAt: '', closesAt: '', secondOpensAt: '', secondClosesAt: '' }

/** "08:00:00" → "08:00" (the API reads either; inputs show minutes). */
const hhmm = (value: string | null | undefined): string => (value ? value.slice(0, 5) : '')

function emptyWeek(): WeekForm {
  return Object.fromEntries(WEEKDAYS.map((day) => [day, { ...CLOSED_DAY }])) as WeekForm
}

export function hoursToForm(read: OpeningHoursRead | undefined): HoursForm {
  const weeks = Object.fromEntries(DEPARTMENTS.map((d) => [d, emptyWeek()])) as Record<LocationDepartment, WeekForm>
  for (const row of read?.hours ?? []) {
    weeks[row.department][row.weekday] = {
      open: true,
      opensAt: hhmm(row.opensAt),
      closesAt: hhmm(row.closesAt),
      secondOpensAt: hhmm(row.secondOpensAt),
      secondClosesAt: hhmm(row.secondClosesAt),
    }
  }
  return { perDepartment: read?.perDepartment ?? false, weeks }
}

/** Turning "differs per department" on starts every department from the one set. */
export function withPerDepartment(form: HoursForm, perDepartment: boolean): HoursForm {
  if (perDepartment === form.perDepartment) return form
  if (!perDepartment) return { ...form, perDepartment }
  const one = form.weeks.sales
  const copy = (): WeekForm => Object.fromEntries(WEEKDAYS.map((d) => [d, { ...one[d] }])) as WeekForm
  return { perDepartment, weeks: { sales: copy(), service: copy(), parts: copy() } }
}

/** A day's entries as the API takes them, or null when they cannot be true. */
function dayToApi(day: DayForm): Omit<OpeningHoursDay, 'department' | 'weekday'> | null {
  if (!day.opensAt || !day.closesAt || day.opensAt >= day.closesAt) return null
  const hasSecond = day.secondOpensAt !== '' || day.secondClosesAt !== ''
  if (!hasSecond) return { opensAt: day.opensAt, closesAt: day.closesAt, secondOpensAt: null, secondClosesAt: null }
  if (!day.secondOpensAt || !day.secondClosesAt) return null
  if (day.secondOpensAt <= day.closesAt || day.secondOpensAt >= day.secondClosesAt) return null
  return { opensAt: day.opensAt, closesAt: day.closesAt, secondOpensAt: day.secondOpensAt, secondClosesAt: day.secondClosesAt }
}

export interface InvalidDay {
  department: LocationDepartment | null
  weekday: number
}

/** The PUT body, or the first day that cannot be true. */
export function formToApi(form: HoursForm): { body: OpeningHoursUpdate } | { invalid: InvalidDay } {
  const departments: (LocationDepartment | null)[] = form.perDepartment ? DEPARTMENTS : [null]
  const hours: OpeningHoursDay[] = []
  for (const department of departments) {
    const week = form.weeks[department ?? 'sales']
    for (const weekday of WEEKDAYS) {
      const day = week[weekday]
      if (!day.open) continue
      const api = dayToApi(day)
      if (api === null) return { invalid: { department, weekday } }
      hours.push(department === null ? { weekday, ...api } : { department, weekday, ...api })
    }
  }
  return { body: { perDepartment: form.perDepartment, hours } }
}

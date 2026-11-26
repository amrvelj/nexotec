import { describe, expect, it } from 'vitest'
import { formToWeek, weekToForm } from './workingHours'

// KAN-808 — the working-week editor's form, mirroring the API's checks
// (planning_working_hours_invalid) so a slip is named before the PUT.

describe('a person’s working week (KAN-808)', () => {
  it('round-trips a week with a lunch break; a day not worked is not sent', () => {
    const week = weekToForm([
      { weekday: 1, startsAt: '07:30:00', endsAt: '12:00:00', secondStartsAt: '13:00:00', secondEndsAt: '17:00:00' },
      { weekday: 5, startsAt: '07:30:00', endsAt: '12:00:00', secondStartsAt: null, secondEndsAt: null },
    ])
    expect(week[2].works).toBe(false)
    expect(formToWeek(week)).toEqual({
      body: {
        days: [
          { weekday: 1, startsAt: '07:30', endsAt: '12:00', secondStartsAt: '13:00', secondEndsAt: '17:00' },
          { weekday: 5, startsAt: '07:30', endsAt: '12:00', secondStartsAt: null, secondEndsAt: null },
        ],
      },
    })
  })

  it('names the first day whose times cannot be true', () => {
    const week = weekToForm([])
    week[3] = { works: true, startsAt: '08:00', endsAt: '12:00', secondStartsAt: '11:00', secondEndsAt: '17:00' }
    expect(formToWeek(week)).toEqual({ invalidWeekday: 3 })
    week[3] = { works: true, startsAt: '12:00', endsAt: '08:00', secondStartsAt: '', secondEndsAt: '' }
    expect(formToWeek(week)).toEqual({ invalidWeekday: 3 })
    week[3] = { works: true, startsAt: '08:00', endsAt: '12:00', secondStartsAt: '13:00', secondEndsAt: '' }
    expect(formToWeek(week)).toEqual({ invalidWeekday: 3 })
  })
})

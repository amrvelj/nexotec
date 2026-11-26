import { describe, expect, it } from 'vitest'
import { clockOf, isoWeekday, minuteOfDay, shiftDay, wallClock, zurichInstant } from './plannerTime'

// KAN-809 — Zurich time whatever the browser's zone (the suite runs in UTC or anywhere).
describe('plannerTime', () => {
  it('reads an instant as Zurich wall-clock, winter and summer', () => {
    expect(wallClock('2027-03-01T08:00:00Z')).toEqual({ date: '2027-03-01', time: '09:00' })
    expect(wallClock('2027-07-01T08:00:00Z')).toEqual({ date: '2027-07-01', time: '10:00' })
    expect(wallClock('2027-03-01T23:30:00Z')).toEqual({ date: '2027-03-02', time: '00:30' })
  })

  it('writes Zurich wall-clock as an instant with its offset, across the clock change', () => {
    expect(zurichInstant('2027-03-01', '09:00')).toBe('2027-03-01T09:00:00+01:00')
    expect(zurichInstant('2027-07-01', '09:00')).toBe('2027-07-01T09:00:00+02:00')
    expect(zurichInstant('2027-03-28', '01:30')).toBe('2027-03-28T01:30:00+01:00')
    expect(zurichInstant('2027-03-28', '04:00')).toBe('2027-03-28T04:00:00+02:00')
    expect(zurichInstant('2027-10-31', '12:00')).toBe('2027-10-31T12:00:00+01:00')
    expect(new Date(zurichInstant('2027-03-01', '09:15')).toISOString()).toBe('2027-03-01T08:15:00.000Z')
  })

  it('places an instant on the shown day, before it or after it', () => {
    expect(minuteOfDay('2027-03-01T08:15:00Z', '2027-03-01')).toBe(9 * 60 + 15)
    expect(minuteOfDay('2027-02-28T20:00:00Z', '2027-03-01')).toBe(-1)
    expect(minuteOfDay('2027-03-02T07:00:00Z', '2027-03-01')).toBeGreaterThan(1440)
  })

  it('moves days and names weekdays and clocks', () => {
    expect(shiftDay('2027-02-28', 1)).toBe('2027-03-01')
    expect(shiftDay('2027-03-01', -1)).toBe('2027-02-28')
    expect(isoWeekday('2027-03-01')).toBe(1)
    expect(isoWeekday('2027-03-07')).toBe(7)
    expect(clockOf(450)).toBe('07:30')
  })
})

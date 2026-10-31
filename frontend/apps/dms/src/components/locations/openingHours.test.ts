import { describe, expect, it } from 'vitest'
import type { OpeningHoursRead } from '../../api/types'
import { formToApi, hoursToForm, withPerDepartment } from './openingHours'

const READ: OpeningHoursRead = {
  locationId: 'loc-1',
  locationVersion: 4,
  perDepartment: false,
  hours: (['sales', 'service', 'parts'] as const).map((department) => ({
    department,
    weekday: 1,
    opensAt: '07:30:00',
    closesAt: '12:00:00',
    secondOpensAt: '13:15:00',
    secondClosesAt: '18:00:00',
  })),
}

// KAN-204 (D-A-27): the one-set mode sends days without a department (the
// API writes them for all three); the per-department mode sends each.
describe('opening hours form', () => {
  it('reads the stored rows into minutes and closed days', () => {
    const form = hoursToForm(READ)
    expect(form.perDepartment).toBe(false)
    expect(form.weeks.sales[1]).toEqual({ open: true, opensAt: '07:30', closesAt: '12:00', secondOpensAt: '13:15', secondClosesAt: '18:00' })
    expect(form.weeks.sales[7].open).toBe(false)
  })

  it('sends one set without departments', () => {
    const result = formToApi(hoursToForm(READ))
    expect(result).toEqual({
      body: { perDepartment: false, hours: [{ weekday: 1, opensAt: '07:30', closesAt: '12:00', secondOpensAt: '13:15', secondClosesAt: '18:00' }] },
    })
  })

  it('copies the one set into every department when it starts to differ', () => {
    const form = withPerDepartment(hoursToForm(READ), true)
    form.weeks.parts[1] = { ...form.weeks.parts[1], open: false }
    const result = formToApi(form)
    expect('body' in result && result.body.hours.map((h) => h.department)).toEqual(['sales', 'service'])
  })

  it('names the first day that cannot be true', () => {
    const form = hoursToForm(READ)
    form.weeks.sales[3] = { open: true, opensAt: '12:00', closesAt: '08:00', secondOpensAt: '', secondClosesAt: '' }
    expect(formToApi(form)).toEqual({ invalid: { department: null, weekday: 3 } })
    form.weeks.sales[3] = { open: true, opensAt: '08:00', closesAt: '12:00', secondOpensAt: '11:00', secondClosesAt: '18:00' }
    expect(formToApi(form)).toEqual({ invalid: { department: null, weekday: 3 } })
    form.weeks.sales[3] = { open: true, opensAt: '08:00', closesAt: '12:00', secondOpensAt: '13:00', secondClosesAt: '' }
    expect(formToApi(form)).toEqual({ invalid: { department: null, weekday: 3 } })
  })
})

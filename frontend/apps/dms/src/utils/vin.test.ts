import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { isMalformedVin, isVinRefusal, normalisedVin } from './vin'

describe('normalisedVin — the API rule, mirrored (KAN-338)', () => {
  it.each([
    ['wvwzzz1kzaw331338', 'WVWZZZ1KZAW331338'],
    ['WVW ZZZ 1KZ AW3 31338', 'WVWZZZ1KZAW331338'],
    [' wvwzzz1kzaw331338\t', 'WVWZZZ1KZAW331338'],
    ['wVw ZZZ1KZAW331338', 'WVWZZZ1KZAW331338'],
  ])('%j is the VIN %s', (input, vin) => {
    expect(normalisedVin(input)).toBe(vin)
  })

  it.each(['WVWZZZ1KZAO331338', 'WVWZZZ1KZAI331338', 'WVWZZZ1KZAQ331338', 'WVWZZZ1KZAW33133', 'WVWZZZ1KZAW3313388', '', '   '])(
    '%j is not a VIN',
    (input) => {
      expect(normalisedVin(input)).toBeNull()
    },
  )
})

describe('isMalformedVin — a 17-character input the API refuses (KAN-351)', () => {
  it.each(['WVWZZZ1KZAO331338', 'wvwzzz1kzai331338', 'WVW ZZZ 1KZ AQ3 31338', 'WVWZZZ1KZAW33133-'])('%j is malformed', (input) => {
    expect(isMalformedVin(input)).toBe(true)
  })

  it.each(['WVWZZZ1KZAW331338', 'wvw zzz 1kz aw3 31338', 'ZH 12345', '123456789', 'WVWZZZ1KZAO', ''])('%j is not', (input) => {
    expect(isMalformedVin(input)).toBe(false)
  })
})

describe('isVinRefusal — a 422 on the body field vin (KAN-351)', () => {
  const refusal = (loc: unknown[]) =>
    new ApiError(422, 'unprocessable_entity', 'Request validation failed.', { errors: [{ loc, msg: 'x', type: 'value_error' }] })

  it('recognises the refusal of vin', () => {
    expect(isVinRefusal(refusal(['body', 'vin']))).toBe(true)
  })

  it('ignores another field, another status and a non-API error', () => {
    expect(isVinRefusal(refusal(['body', 'vehicleLabel']))).toBe(false)
    expect(isVinRefusal(refusal(['query', 'vin']))).toBe(false)
    expect(isVinRefusal(new ApiError(409, 'conflict', 'x', null))).toBe(false)
    expect(isVinRefusal(new Error('network'))).toBe(false)
  })
})

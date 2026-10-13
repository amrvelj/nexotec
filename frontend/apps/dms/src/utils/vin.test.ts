import { describe, expect, it } from 'vitest'
import { normalisedVin } from './vin'

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

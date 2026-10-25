import { describe, expect, it } from 'vitest'
import { addressFromComponents } from './googlePlaces'

// KAN-37: Google's address components onto our five fields. A Swiss street
// address; a place without a house number; the canton
// (administrative_area_level_1) is never read (D-13).

describe('addressFromComponents (KAN-37)', () => {
  it('maps a Swiss street address and ignores the canton', () => {
    expect(
      addressFromComponents([
        { longText: '12', shortText: '12', types: ['street_number'] },
        { longText: 'Bahnhofstrasse', shortText: 'Bahnhofstrasse', types: ['route'] },
        { longText: 'Zürich', shortText: 'Zürich', types: ['locality', 'political'] },
        { longText: 'Zürich', shortText: 'ZH', types: ['administrative_area_level_1', 'political'] },
        { longText: 'Schweiz', shortText: 'CH', types: ['country', 'political'] },
        { longText: '8001', shortText: '8001', types: ['postal_code'] },
      ]),
    ).toEqual({ street: 'Bahnhofstrasse', houseNumber: '12', postalCode: '8001', locality: 'Zürich', country: 'CH' })
  })

  it('leaves a component Google did not return empty, and falls back to a postal town for the locality', () => {
    expect(
      addressFromComponents([
        { longText: 'Hauptstrasse', shortText: 'Hauptstrasse', types: ['route'] },
        { longText: 'Reading', shortText: 'Reading', types: ['postal_town'] },
        { longText: 'United Kingdom', shortText: 'gb', types: ['country', 'political'] },
      ]),
    ).toEqual({ street: 'Hauptstrasse', houseNumber: '', postalCode: '', locality: 'Reading', country: 'GB' })
  })
})

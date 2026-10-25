import { afterEach, describe, expect, it, vi } from 'vitest'
import { addressFromComponents, fetchAddressSuggestions } from './googlePlaces'

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

// The request itself: the region restriction follows the form's country only
// when it is a country code — a half-typed one on the Business forms restricts
// nothing — and a refusal answers `null`, never a list (a refusal is not "no
// match"). `fetch` is stubbed; nothing leaves the test.
describe('fetchAddressSuggestions (KAN-37)', () => {
  afterEach(() => vi.unstubAllGlobals())

  const request = { browserKey: 'k', sessionToken: 't', language: 'de' }
  const stub = (status: number, body: unknown) => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }
  const sentBody = (fetchMock: ReturnType<typeof stub>) => JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)

  it('restricts to a two-letter country and sends no restriction for a half-typed one', async () => {
    let fetchMock = stub(200, { suggestions: [] })
    await fetchAddressSuggestions('Bahnhofstrasse', 'DE', request)
    expect(sentBody(fetchMock).includedRegionCodes).toEqual(['de'])

    fetchMock = stub(200, { suggestions: [] })
    await fetchAddressSuggestions('Bahnhofstrasse', 'D', request)
    expect(sentBody(fetchMock)).not.toHaveProperty('includedRegionCodes')
  })

  it('answers null on a refusal and an empty list on a genuine no-match', async () => {
    stub(403, { error: { code: 403 } })
    expect(await fetchAddressSuggestions('Bahnhofstrasse', 'CH', request)).toBeNull()
    stub(200, { suggestions: [] })
    expect(await fetchAddressSuggestions('Bahnhofstrasse', 'CH', request)).toEqual([])
  })
})

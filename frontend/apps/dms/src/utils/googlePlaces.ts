// KAN-37 (ADR-101) — Google Places Autocomplete, the browser's side of it.
//
// Places API (New) over plain `fetch`, no SDK and no script tag: two calls per
// pick, both under one session token so a run of keystrokes bills as one
// session — autocomplete, restricted to the form's country, then the place's
// address components and nothing else (the field mask). The key is Nexotec's,
// handed to the browser by GET /v1/dealerships/{id}/address-autocomplete while
// the Betrieb's switch is on; its protection is Google Cloud's restriction to
// our origins and to Places API (New), set up and verified at go-live (KAN-451).
//
// Two rules this module keeps (the Places API policies' autocomplete
// exception, which is what lets us store the picked address at all):
// - the suggestion list is never persisted or logged — it lives in the
//   caller's component state until the next keystroke, and nothing here
//   writes to the console, to storage or to our own API (an architecture
//   test pins the last two: this file imports no API client);
// - only the address the advisor picked is written, into the same fields
//   they would have typed; canton is never taken from Google (D-13: the
//   server derives it; the Business forms keep their own picker).
//
// Any failure is never an error on screen: a refusal (a non-OK answer — a
// blocked key, an exhausted quota) is `null`, and no network is `fetch`'s own
// rejection; the caller shows no dropdown for either (a refusal is not "no
// match"), the fields are plain inputs and the advisor types on.

const PLACES_ORIGIN = 'https://places.googleapis.com'

/** One suggestion as Google returns it; `text` is the full line, the two parts are for the dropdown. */
export interface AddressSuggestion {
  placeId: string
  text: string
  mainText: string
  secondaryText: string
}

/** The address components we write into the form. Empty strings for what Google did not return. */
export interface ResolvedAddress {
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  /** ISO 3166-1 alpha-2, upper-cased; '' when Google returned none. */
  country: string
}

interface PlacesRequest {
  browserKey: string
  sessionToken: string
  language: string
  signal?: AbortSignal
}

/** A fresh Autocomplete session token — a UUID v4, as Google asks. */
export function newSessionToken(): string {
  return crypto.randomUUID()
}

// Street addresses and buildings only: a run of keystrokes should offer
// "Bahnhofstrasse 12, 8001 Zürich", not the railway station or a café.
// `route` keeps a street the advisor has typed without its number.
const PRIMARY_TYPES = ['street_address', 'premise', 'subpremise', 'route']

interface AutocompleteResponse {
  suggestions?: Array<{
    placePrediction?: {
      placeId?: string
      text?: { text?: string }
      structuredFormat?: { mainText?: { text?: string }; secondaryText?: { text?: string } }
    }
  }>
}

/**
 * The suggestions for `input`, restricted to `country` (ISO 3166-1 alpha-2;
 * a value that is not two letters — a half-typed country on the Business
 * forms — restricts nothing). `null` when Google refused or failed, so the
 * caller can tell a refusal from a genuine "no match"; an aborted request
 * rejects with the AbortError so a superseded keystroke is told apart too.
 */
export async function fetchAddressSuggestions(
  input: string,
  country: string,
  { browserKey, sessionToken, language, signal }: PlacesRequest,
): Promise<AddressSuggestion[] | null> {
  const region = /^[A-Za-z]{2}$/.test(country) ? country.toLowerCase() : null
  const response = await fetch(`${PLACES_ORIGIN}/v1/places:autocomplete`, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json', 'X-Goog-Api-Key': browserKey },
    body: JSON.stringify({
      input,
      sessionToken,
      languageCode: language,
      ...(region ? { includedRegionCodes: [region] } : {}),
      includedPrimaryTypes: PRIMARY_TYPES,
    }),
  })
  if (!response.ok) return null
  const body = (await response.json().catch(() => null)) as AutocompleteResponse | null
  const suggestions: AddressSuggestion[] = []
  for (const entry of body?.suggestions ?? []) {
    const prediction = entry.placePrediction
    if (!prediction?.placeId) continue
    const text = prediction.text?.text ?? ''
    suggestions.push({
      placeId: prediction.placeId,
      text,
      mainText: prediction.structuredFormat?.mainText?.text ?? text,
      secondaryText: prediction.structuredFormat?.secondaryText?.text ?? '',
    })
  }
  return suggestions
}

interface PlaceDetailsResponse {
  addressComponents?: Array<{ longText?: string; shortText?: string; types?: string[] }>
}

/**
 * The address components of one picked place, in `language`. The same
 * session token as the suggestions closes the billing session. `null` on
 * any failure — the caller then leaves the fields as they are.
 */
export async function resolvePlaceAddress(
  placeId: string,
  { browserKey, sessionToken, language, signal }: PlacesRequest,
): Promise<ResolvedAddress | null> {
  const params = new URLSearchParams({ sessionToken, languageCode: language })
  const response = await fetch(`${PLACES_ORIGIN}/v1/places/${encodeURIComponent(placeId)}?${params}`, {
    signal,
    headers: { 'X-Goog-Api-Key': browserKey, 'X-Goog-FieldMask': 'addressComponents' },
  })
  if (!response.ok) return null
  const body = (await response.json().catch(() => null)) as PlaceDetailsResponse | null
  if (!body?.addressComponents) return null
  return addressFromComponents(body.addressComponents)
}

/** Exported for its own test: the mapping from Google's component types to our five fields. */
export function addressFromComponents(components: NonNullable<PlaceDetailsResponse['addressComponents']>): ResolvedAddress {
  const first = (...types: string[]): { longText?: string; shortText?: string } | undefined =>
    types.map((type) => components.find((c) => c.types?.includes(type))).find((c) => c !== undefined)
  // A Swiss locality is `locality`; some countries name it `postal_town`
  // (GB) or only a sublocality. `administrative_area_level_1` — the canton —
  // is deliberately not read (D-13).
  return {
    street: first('route')?.longText ?? '',
    houseNumber: first('street_number')?.longText ?? '',
    postalCode: first('postal_code')?.longText ?? '',
    locality: first('locality', 'postal_town', 'sublocality_level_1', 'administrative_area_level_3')?.longText ?? '',
    country: (first('country')?.shortText ?? '').toUpperCase(),
  }
}

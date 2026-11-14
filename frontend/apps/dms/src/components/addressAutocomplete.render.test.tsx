// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { customer } from '../test/fixtures'
import { CustomerCreateFlow } from '../modules/customer/components/CustomerCreateFlow'
import { CustomerDetailPage } from '../modules/customer/pages/CustomerDetailPage'
import { BusinessPage } from '../pages/BusinessPage'

// KAN-37 (ADR-101) — Google Places Autocomplete on the address forms. One
// search field above each address block, shown only while the Betrieb's
// switch is on and the platform holds a key; a pick fills street, house
// number, postal code, locality and country, every field stays editable,
// line 2 is never touched; both Google calls carry the key and ONE session
// token, the suggestions are restricted to the form's country and the
// dropdown carries the Google logo; a Google failure leaves the fields as
// plain inputs and says nothing. The fake backend answers Google's two
// routes by pathname; their handlers record the host they were called on.

const KEY = 'AIza-test-browser-key'

const SUGGESTIONS = {
  suggestions: [
    {
      placePrediction: {
        placeId: 'place-bahnhofstrasse-12',
        text: { text: 'Bahnhofstrasse 12, 8001 Zürich, Schweiz' },
        structuredFormat: { mainText: { text: 'Bahnhofstrasse 12' }, secondaryText: { text: '8001 Zürich, Schweiz' } },
      },
    },
    {
      placePrediction: {
        placeId: 'place-bahnhofstrasse-120',
        text: { text: 'Bahnhofstrasse 120, 8001 Zürich, Schweiz' },
        structuredFormat: { mainText: { text: 'Bahnhofstrasse 120' }, secondaryText: { text: '8001 Zürich, Schweiz' } },
      },
    },
  ],
}

// Place details per id: Bahnhofstrasse 12 is in 8001 Zürich; "120" is answered
// as 3011 Bern, so a Business pick that keeps the seat's postal code (3011) can
// be told from one that moves it.
const DETAILS = {
  addressComponents: [
    { longText: '12', shortText: '12', types: ['street_number'] },
    { longText: 'Bahnhofstrasse', shortText: 'Bahnhofstrasse', types: ['route'] },
    { longText: 'Zürich', shortText: 'Zürich', types: ['locality', 'political'] },
    { longText: 'Zürich', shortText: 'ZH', types: ['administrative_area_level_1', 'political'] },
    { longText: 'Schweiz', shortText: 'CH', types: ['country', 'political'] },
    { longText: '8001', shortText: '8001', types: ['postal_code'] },
  ],
}

const configOn: FakeRoute = { method: 'GET', match: /\/address-autocomplete$/, handler: () => ({ enabled: true, browserKey: KEY }) }
const googleHosts: string[] = []
const googleRoutes = (): FakeRoute[] => [
  {
    method: 'POST',
    match: /^\/places:autocomplete$/,
    handler: (req) => {
      googleHosts.push(req.url.host)
      return SUGGESTIONS
    },
  },
  {
    method: 'GET',
    match: /^\/places\/[^/]+$/,
    handler: (req) => {
      googleHosts.push(req.url.host)
      if (req.pathname.endsWith('/place-bahnhofstrasse-120')) {
        return {
          addressComponents: [
            { longText: '120', shortText: '120', types: ['street_number'] },
            { longText: 'Bahnhofstrasse', shortText: 'Bahnhofstrasse', types: ['route'] },
            { longText: 'Bern', shortText: 'Bern', types: ['locality', 'political'] },
            { longText: 'Schweiz', shortText: 'CH', types: ['country', 'political'] },
            { longText: '3011', shortText: '3011', types: ['postal_code'] },
          ],
        }
      }
      return DETAILS
    },
  },
]
const createRoutes = (): FakeRoute[] => [
  { match: /^\/customers\/duplicate-check$/, handler: () => ({ items: [], nextCursor: null }) },
  { method: 'POST', match: /^\/customers$/, handler: (req) => ({ ...customer({ id: 'new-1' }), ...(req.body as object) }) },
]

const searchLabel = () => i18n.t('addressSearch.label')
const field = (key: string) => screen.getByLabelText(i18n.t(`customerCreate.fields.${key}`)) as HTMLInputElement

async function openAddressBlock(user: ReturnType<typeof userEvent.setup>) {
  renderWithProviders(<CustomerCreateFlow onSuccess={() => {}} onCancel={() => {}} />)
  await user.click(screen.getByRole('button', { name: i18n.t('customerCreate.actions.next') }))
  await screen.findByText(i18n.t('customerDetail.contactPoints.phoneNumbers'))
  await user.click(screen.getByLabelText(i18n.t('customerCreate.fields.hasAddress')))
}

afterEach(async () => {
  await i18n.changeLanguage('de')
  googleHosts.length = 0
})

describe('address autocomplete — the create wizard (KAN-37)', () => {
  it('a pick fills the five fields from Google, under one session token, restricted to the form country, with the Google logo shown', async () => {
    const backend = installFakeBackend([configOn, ...googleRoutes(), ...createRoutes()])
    const user = userEvent.setup()
    await openAddressBlock(user)

    await user.type(await screen.findByRole('textbox', { name: (n) => n.startsWith(searchLabel()) }), 'Bahnhofstrasse 12 Zür')
    const option = await screen.findByRole('option', { name: /Bahnhofstrasse 12\b/ })
    expect(screen.getByRole('option', { name: /Bahnhofstrasse 120/ })).toBeInTheDocument()
    // The attribution the Places API policies require when results are shown without a map: the logo, at least 16 px.
    expect(screen.getByRole('img', { name: i18n.t('addressSearch.attribution') })).toHaveAttribute('height', '16')

    await user.click(option)
    await waitFor(() => expect(field('street').value).toBe('Bahnhofstrasse'))
    expect(field('houseNumber').value).toBe('12')
    expect(field('postalCode').value).toBe('8001')
    expect(field('locality').value).toBe('Zürich')
    expect(field('line2').value).toBe('')

    // Both calls went to Google with the key and the same session token; the
    // search was restricted to the form's country (CH) in the UI language.
    const autocomplete = backend.callsTo(/^\/places:autocomplete$/, 'POST')
    const details = backend.callsTo(/^\/places\/place-bahnhofstrasse-12$/, 'GET')
    expect(autocomplete.length).toBeGreaterThanOrEqual(1)
    expect(details).toHaveLength(1)
    const lastSearch = autocomplete[autocomplete.length - 1]
    expect(lastSearch.headers.get('X-Goog-Api-Key')).toBe(KEY)
    expect(details[0].headers.get('X-Goog-Api-Key')).toBe(KEY)
    expect(details[0].headers.get('X-Goog-FieldMask')).toBe('addressComponents')
    const body = lastSearch.body as { sessionToken: string; includedRegionCodes: string[]; languageCode: string; input: string }
    expect(body.includedRegionCodes).toEqual(['ch'])
    expect(body.languageCode).toBe('de')
    expect(details[0].params.get('sessionToken')).toBe(body.sessionToken)
    expect(body.sessionToken).toMatch(/^[0-9a-f-]{36}$/)
    // Every call of the run used the one token, and every call went to Google's origin.
    for (const call of autocomplete) expect((call.body as { sessionToken: string }).sessionToken).toBe(body.sessionToken)
    expect(new Set(googleHosts)).toEqual(new Set(['places.googleapis.com']))

    // Every field stays editable after the pick.
    await user.clear(field('houseNumber'))
    await user.type(field('houseNumber'), '12a')
    expect(field('houseNumber').value).toBe('12a')

    // The pick ended the session: a new run carries a new token.
    const search = screen.getByRole('textbox', { name: (n) => n.startsWith(searchLabel()) })
    await user.clear(search)
    await user.type(search, 'Seestrasse 5')
    await screen.findByRole('option', { name: /Bahnhofstrasse 120/ })
    const later = backend.callsTo(/^\/places:autocomplete$/, 'POST')
    const nextToken = (later[later.length - 1].body as { sessionToken: string }).sessionToken
    expect(nextToken).toMatch(/^[0-9a-f-]{36}$/)
    expect(nextToken).not.toBe(body.sessionToken)

    // An abandoned run ends too: clearing the field (and letting the debounce
    // see it empty) spends the token, so the next keystrokes are a new session.
    await user.clear(search)
    await new Promise((resolve) => setTimeout(resolve, 400))
    await user.type(search, 'Seestrasse 7')
    await waitFor(() => expect(backend.callsTo(/^\/places:autocomplete$/, 'POST').length).toBeGreaterThan(later.length))
    const afterClear = backend.callsTo(/^\/places:autocomplete$/, 'POST')
    const clearedToken = (afterClear[afterClear.length - 1].body as { sessionToken: string }).sessionToken
    expect(clearedToken).toMatch(/^[0-9a-f-]{36}$/)
    expect(clearedToken).not.toBe(nextToken)

    // The suggestion list never went to our API: our backend saw only the config read and the duplicate check.
    const ours = backend.calls.filter((c) => !c.pathname.startsWith('/places'))
    expect(ours.every((c) => !JSON.stringify(c.body ?? '').includes('Bahnhofstrasse 120'))).toBe(true)
  })

  it('renders no search field while the Betrieb switch is off or the platform holds no key', async () => {
    installFakeBackend(createRoutes()) // the fake backend's default: enabled false
    const user = userEvent.setup()
    await openAddressBlock(user)

    expect(screen.getByLabelText(i18n.t('customerCreate.fields.street'))).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: (n) => n.startsWith(searchLabel()) })).toBeNull()
  })

  it('a Google refusal leaves the fields as plain inputs, shows no dropdown and says nothing', async () => {
    const backend = installFakeBackend([
      configOn,
      { method: 'POST', match: /^\/places:autocomplete$/, handler: () => status(403, { error: { code: 403, message: 'blocked' } }) },
      ...createRoutes(),
    ])
    const user = userEvent.setup()
    await openAddressBlock(user)

    await user.type(await screen.findByRole('textbox', { name: (n) => n.startsWith(searchLabel()) }), 'Bahnhofstrasse 12')
    await waitFor(() => expect(backend.callsTo(/^\/places:autocomplete$/, 'POST').length).toBeGreaterThanOrEqual(1))
    expect(screen.queryByRole('option')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
    // A refusal is not "no match": no dropdown, no empty state, no logo.
    expect(screen.queryByText(i18n.t('addressSearch.noResults'))).toBeNull()
    expect(screen.queryByRole('img', { name: i18n.t('addressSearch.attribution') })).toBeNull()
    await user.type(field('street'), 'Bahnhofstrasse')
    expect(field('street').value).toBe('Bahnhofstrasse')
  })

  it('every string of the search field comes from the active bundle', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([configOn, ...googleRoutes(), ...createRoutes()])
    const user = userEvent.setup()
    await openAddressBlock(user)
    const input = await screen.findByRole('textbox', { name: (n) => n.startsWith('Rechercher une adresse') })
    expect(input).toHaveAttribute('placeholder', 'Rue, numéro, localité…')
    expect(document.body.textContent).not.toMatch(/⚠ MISSING I18N KEY/)
  })
})

describe('address autocomplete — the detail screen and the Business screen (KAN-37)', () => {
  it('the customer detail address editor offers the search field and a pick fills the draft', async () => {
    installFakeBackend([
      configOn,
      ...googleRoutes(),
      { match: /^\/customers\/c1\/phones$/, handler: () => ({ items: [] }) },
      { match: /^\/customers\/c1\/emails$/, handler: () => ({ items: [] }) },
      { match: /^\/customers\/c1\/vehicles$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1\/external-ids$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1\/audit-log$/, handler: () => ({ items: [], nextCursor: null }) },
      { match: /^\/customers\/c1$/, handler: () => customer({ id: 'c1', address: null }) },
    ])
    renderWithProviders(
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetailPage />} />
      </Routes>,
      { route: '/customers/c1' },
    )
    const user = userEvent.setup()
    await screen.findAllByText(i18n.t('customerDetail.overview.cards.address'))
    const title = screen.getAllByText(i18n.t('customerDetail.overview.cards.address')).find((el) => el.tagName === 'DIV')!
    const card = title.parentElement!.parentElement as HTMLElement
    await user.click(within(card).getAllByText(i18n.t('customerDetail.overview.notSet'))[0])

    await user.type(await screen.findByRole('textbox', { name: (n) => n.startsWith(searchLabel()) }), 'Bahnhofstrasse 12')
    await user.click(await screen.findByRole('option', { name: /Bahnhofstrasse 12\b/ }))
    const street = screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.street')) as HTMLInputElement
    await waitFor(() => expect(street.value).toBe('Bahnhofstrasse'))
    expect((screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.postalCode')) as HTMLInputElement).value).toBe('8001')
    expect((screen.getByLabelText(i18n.t('customerDetail.overview.addressForm.line2')) as HTMLInputElement).value).toBe('')
  })

  it("the Business screen's seat address offers the search field; a pick that moves the postal code clears the canton for the manager to re-pick", async () => {
    const backend = installFakeBackend([
      configOn,
      ...googleRoutes(),
      {
        method: 'GET',
        match: /\/dealerships\/dealership-1$/,
        handler: () => ({
          id: 'dealership-1',
          dealerGroupId: 'group-1',
          legalName: 'Test Motors AG',
          tradeName: null,
          franchiseType: 'independent',
          oemAffiliations: null,
          address: { street: 'Alte Gasse', houseNumber: '1', postalCode: '3011', locality: 'Bern', canton: 'BE', country: 'CH' },
          correspondenceAddress: null,
          billingAddress: null,
          phone: '+41441234567',
          uid: 'CHE-123.456.788',
          vatNumber: null,
          vatLiable: false,
          status: 'active',
          vatRate: null,
          ageingAlertThresholds: null,
          legalForm: null,
          commercialRegisterOffice: null,
          hrNumber: null,
          astraOperatorNumber: null,
          collectiveVehicleLicences: null,
          associationMemberships: null,
          version: 1,
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
          createdBy: null,
          updatedBy: null,
        }),
      },
      { method: 'GET', match: /\/dealerships\/dealership-1\/settings$/, handler: () => status(404) },
      { method: 'GET', match: /\/dealerships\/dealership-1\/bank-accounts$/, handler: () => status(404) },
      { method: 'GET', match: /\/reference-data\//, handler: () => ({ items: [], nextCursor: null }) },
    ])
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const user = userEvent.setup()

    const addresses = within(await screen.findByRole('region', { name: 'Adressen' }))
    const search = await addresses.findByRole('textbox', { name: (n) => n.startsWith(searchLabel()) })
    // A pick that keeps the seat's postal code (3011) keeps the manager's canton.
    await user.type(search, 'Bahnhofstrasse 12')
    await user.click(await screen.findByRole('option', { name: /Bahnhofstrasse 120/ }))
    await waitFor(() => expect((document.getElementById('seat-street') as HTMLInputElement).value).toBe('Bahnhofstrasse'))
    expect((document.getElementById('seat-house') as HTMLInputElement).value).toBe('120')
    expect((document.getElementById('seat-postal') as HTMLInputElement).value).toBe('3011')
    expect((document.getElementById('seat-canton') as HTMLInputElement).value).toBe('Bern')
    // A pick that moves it (8001) clears the canton. (A different query than
    // the first run: the same text retyped inside the debounce window is the
    // same debounced value and starts no new search.)
    const searched = backend.callsTo(/^\/places:autocomplete$/, 'POST').length
    await user.clear(search)
    await user.type(search, 'Bahnhofstrasse 12 Zürich')
    await waitFor(() => expect(backend.callsTo(/^\/places:autocomplete$/, 'POST').length).toBeGreaterThan(searched))
    await user.click(await screen.findByRole('option', { name: /Bahnhofstrasse 12\b/ }))
    await waitFor(() => expect((document.getElementById('seat-postal') as HTMLInputElement).value).toBe('8001'))
    expect((document.getElementById('seat-locality') as HTMLInputElement).value).toBe('Zürich')
    // Google's administrative_area_level_1 (ZH) is discarded and the old canton (BE) no longer fits 8001:
    // the required Select is cleared, the manager picks the canton — never Google.
    expect((document.getElementById('seat-canton') as HTMLInputElement).value).toBe('')
    expect(addresses.getByRole('button', { name: 'Speichern' })).toBeInTheDocument()
  })
})

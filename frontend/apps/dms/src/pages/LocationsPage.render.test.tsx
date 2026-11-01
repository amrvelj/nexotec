// @vitest-environment jsdom
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { renderWithProviders } from '../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { location } from '../test/fixtures'
import { LocationDetailPage } from './LocationDetailPage'
import { LocationsListPage } from './LocationsListPage'

// jsdom has no layout, so TanStack Virtual windows down to zero rows — the
// "render every row" shim the other grid tests use (MappingGapsQueue).
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

// KAN-204 — Dealer Administration › Locations: the list with the row menu
// (ADR-061; the last active site's deactivation disabled with its reason,
// FR-A-03), the create dialog (Idempotency-Key), and one location's screen:
// sections, the hours editor (one set by default, per department behind the
// toggle — D-A-27), the holidays of the picked calendar, closures, the map
// pin, the overrides collapsed, and a role without the write reading only.

const NORD = location({ id: 'loc-1', name: 'Zürich Nord', geoLat: 47.4141, geoLng: 8.5469, holidayCalendarId: 'cal-zh', validFrom: '2026-11-01' })
const SUED = location({ id: 'loc-2', name: 'Winterthur', code: 'WI', locationTypes: ['body_shop'], isActive: false })

const CALENDARS = {
  items: [
    {
      id: 'cal-zh',
      code: 'ZH',
      canton: 'ZH',
      labelDe: 'Zürich',
      labelFr: 'Zurich',
      labelIt: 'Zurigo',
      labelEn: 'Zurich',
      source: 'SECO',
      holidays: [],
    },
    {
      id: 'cal-ag-baden',
      code: 'AG-BADEN',
      canton: 'AG',
      labelDe: 'Aargau – Bezirk Baden (ohne Bergdietikon)',
      labelFr: 'Argovie – district de Baden (sans Bergdietikon)',
      labelIt: 'Argovia – distretto di Baden (senza Bergdietikon)',
      labelEn: 'Aargau – Baden district (excluding Bergdietikon)',
      source: 'SECO',
      holidays: [],
    },
    {
      id: 'cal-ag-zurzach',
      code: 'AG-ZURZACH',
      canton: 'AG',
      labelDe: 'Aargau – Bezirk Zurzach',
      labelFr: 'Argovie – district de Zurzach',
      labelIt: 'Argovia – distretto di Zurzach',
      labelEn: 'Aargau – Zurzach district',
      source: 'SECO',
      holidays: [],
    },
  ],
}

const DATES = {
  calendarId: 'cal-zh',
  year: 2026,
  items: [
    { date: '2026-01-01', code: 'new_year', labelDe: 'Neujahrstag', labelFr: 'Nouvel an', labelIt: 'Capodanno', labelEn: "New Year's Day", afternoonOnly: false },
    { date: '2026-05-01', code: 'labour_day', labelDe: 'Tag der Arbeit', labelFr: 'Fête du travail', labelIt: 'Festa del lavoro', labelEn: 'Labour Day', afternoonOnly: true },
  ],
}

const ONE_SET_HOURS = {
  locationId: 'loc-1',
  locationVersion: 1,
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

function routes(extra: FakeRoute[] = []): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1\/locations$/, handler: () => ({ items: [NORD, SUED] }) },
    { method: 'GET', match: /\/dealerships\/dealership-1\/locations\/loc-1$/, handler: () => NORD },
    { method: 'GET', match: /\/locations\/loc-1\/opening-hours$/, handler: () => ONE_SET_HOURS },
    {
      method: 'GET',
      match: /\/locations\/loc-1\/closures$/,
      handler: () => ({
        items: [
          { id: 'cl-1', locationId: 'loc-1', startsOn: '2026-07-20', endsOn: '2026-08-07', reason: 'Betriebsferien', version: 1, createdAt: '', updatedAt: '', createdBy: null, updatedBy: null },
        ],
      }),
    },
    { method: 'GET', match: /\/holiday-calendars$/, handler: () => CALENDARS },
    { method: 'GET', match: /\/holiday-calendars\/cal-zh\/dates$/, handler: () => DATES },
    { method: 'GET', match: /\/dealerships\/dealership-1\/bank-accounts$/, handler: () => status(404, { error: { code: 'not_found', message: 'Not found.', details: null } }) },
  ]
}

const READ_ONLY_SESSION: FakeRoute = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: { id: 'user-1', dealershipId: 'dealership-1', firstName: 'Sara', lastName: 'Sales', email: 's@example.ch', phone: null, role: 'salesperson', accessRoles: ['sales'], isDealerManager: false, employmentStatus: 'active', authIdentityId: 'a', status: 'active', version: 1, createdAt: '', updatedAt: '' },
    activeDealership: { id: 'dealership-1', legalName: 'Test Motors AG' },
    memberships: [{ id: 'dealership-1', legalName: 'Test Motors AG' }],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ALL_GATED_CAPABILITIES.filter((c) => c !== 'locations'),
  }),
}

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/administration/locations/:locationId" element={<LocationDetailPage />} />
    </Routes>,
    { route: '/administration/locations/loc-1' },
  )
}

describe('LocationsListPage (KAN-204)', () => {
  let tz: string | undefined
  beforeAll(() => {
    // A date-only field keeps its calendar day west of Greenwich.
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('lists the sites with code, type, calendar, validity and status', async () => {
    installFakeBackend(routes())
    renderWithProviders(<LocationsListPage />, { route: '/administration/locations' })
    const grid = within(await screen.findByRole('table'))
    expect(await grid.findByText('Zürich Nord')).toBeInTheDocument()
    expect(grid.getByText('ZH-NORD')).toBeInTheDocument()
    expect(grid.getByText('Showroom, Werkstatt')).toBeInTheDocument()
    expect(grid.getAllByText('Thurgauerstrasse 40, 8050 Zürich')).toHaveLength(2)
    expect(grid.getByText('01.11.2026')).toBeInTheDocument()
    expect(grid.getByText('Carrosserie')).toBeInTheDocument()
    expect(grid.getByText('Aktiv')).toBeInTheDocument()
    expect(grid.getByText('Deaktiviert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Neuer Standort' })).toBeInTheDocument()
  })

  it('creates a site with an Idempotency-Key and the region calendar the dealer picks', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'POST', match: /\/dealerships\/dealership-1\/locations$/, handler: (req) => ({ ...location({ id: 'loc-9' }), ...(req.body as object) }) }]),
    )
    const user = userEvent.setup()
    renderWithProviders(
      <Routes>
        <Route path="/administration/locations" element={<LocationsListPage />} />
        <Route path="/administration/locations/:locationId" element={<p>detail opened</p>} />
      </Routes>,
      { route: '/administration/locations' },
    )
    await user.click(await screen.findByRole('button', { name: 'Neuer Standort' }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Kürzel') }), 'ag-baden')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Name') }), 'Baden')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Strasse') }), 'Mellingerstrasse')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Nr.') }), '12')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('PLZ') }), '5400')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Ort') }), 'Baden')
    await user.click(dialog.getByRole('textbox', { name: (n) => n.startsWith('Kanton') }))
    await user.click(await screen.findByRole('option', { name: 'Aargau' }))
    // Aargau's holidays differ by district: the dealer picks the region.
    await user.click(await dialog.findByRole('textbox', { name: (n) => n.startsWith('Feiertagskalender') }))
    await user.click(await screen.findByRole('option', { name: 'Aargau – Bezirk Baden (ohne Bergdietikon)' }))
    await user.click(dialog.getByRole('button', { name: 'Anlegen' }))

    await waitFor(() => expect(backend.callsTo(/\/locations$/, 'POST')).toHaveLength(1))
    const call = backend.callsTo(/\/locations$/, 'POST')[0]
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(call.body).toMatchObject({
      code: 'AG-BADEN',
      name: 'Baden',
      addressStreet: 'Mellingerstrasse',
      addressPostalCode: '5400',
      addressCanton: 'AG',
      holidayCalendarId: 'cal-ag-baden',
    })
    expect(await screen.findByText('detail opened')).toBeInTheDocument()
  })

  it('offers no create to a role without the write', async () => {
    installFakeBackend(routes([READ_ONLY_SESSION]))
    renderWithProviders(<LocationsListPage />, { route: '/administration/locations' })
    expect(await screen.findByText('Zürich Nord')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Neuer Standort' })).toBeNull()
  })
})

describe('LocationDetailPage (KAN-204)', () => {
  it('renders every section, the map pin, this year\'s holidays and the closures', async () => {
    installFakeBackend(routes())
    renderDetail()
    expect(await screen.findByRole('heading', { name: 'Stammdaten' })).toBeInTheDocument()
    for (const name of ['Kontakte', 'Adresse und Karte', 'Öffnungszeiten', 'Feiertage und Schliessungen', 'Abweichungen vom Betrieb']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }
    // The code is fixed once saved.
    expect(screen.getByDisplayValue('ZH-NORD')).toHaveAttribute('readonly')
    // The pin: the coordinates and a link to OpenStreetMap, no tiles.
    const pin = screen.getByRole('group', { name: 'Kartenpunkt' })
    expect(within(pin).getByText('47.414100, 8.546900')).toBeInTheDocument()
    expect(within(pin).getByRole('link', { name: 'In OpenStreetMap öffnen' })).toHaveAttribute('href', expect.stringContaining('mlat=47.4141&mlon=8.5469'))
    // The holidays of the picked calendar, Solothurn-style afternoons marked.
    const holidays = await screen.findByRole('table', { name: /^Feiertage \d{4}$/ })
    expect(within(holidays).getByText('Neujahrstag')).toBeInTheDocument()
    expect(within(holidays).getByText('(ab Mittag)')).toBeInTheDocument()
    expect(await screen.findByText('Betriebsferien')).toBeInTheDocument()
    // The overrides are collapsed; the bank account is absent where banking is a 404 (ADR-100).
    expect(screen.queryByRole('textbox', { name: (n) => n.startsWith('Adressblock im Briefkopf') })).toBeNull()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Briefkopf und Bankverbindung dieses Standorts' }))
    expect(screen.getByRole('textbox', { name: (n) => n.startsWith('Adressblock im Briefkopf') })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Bankverbindung' })).toBeNull()
  })

  it('saves one set of hours without departments, with the location\'s If-Match', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PUT', match: /\/locations\/loc-1\/opening-hours$/, handler: () => ({ ...ONE_SET_HOURS, locationVersion: 2 }) }]),
    )
    const user = userEvent.setup()
    renderDetail()
    const hours = within(await screen.findByRole('region', { name: 'Öffnungszeiten' }))
    await waitFor(() => expect(hours.getByRole('checkbox', { name: 'Montag' })).toBeChecked())
    await user.click(hours.getByRole('checkbox', { name: 'Samstag' }))
    await user.click(hours.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/opening-hours$/, 'PUT')).toHaveLength(1))
    const call = backend.callsTo(/\/opening-hours$/, 'PUT')[0]
    expect(call.headers.get('If-Match')).toBe('1')
    expect(call.body).toEqual({
      perDepartment: false,
      hours: [
        { weekday: 1, opensAt: '07:30', closesAt: '12:00', secondOpensAt: '13:15', secondClosesAt: '18:00' },
        { weekday: 6, opensAt: '08:00', closesAt: '18:00', secondOpensAt: null, secondClosesAt: null },
      ],
    })
  })

  it('splits the hours per department behind the toggle', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PUT', match: /\/locations\/loc-1\/opening-hours$/, handler: () => ({ ...ONE_SET_HOURS, perDepartment: true, locationVersion: 2 }) }]),
    )
    const user = userEvent.setup()
    renderDetail()
    const hours = within(await screen.findByRole('region', { name: 'Öffnungszeiten' }))
    await waitFor(() => expect(hours.getByRole('checkbox', { name: 'Montag' })).toBeChecked())
    await user.click(hours.getByRole('switch', { name: (n) => n.startsWith('Zeiten je Abteilung unterschiedlich') }))
    // Every department starts from the one set; Parts closes on Monday.
    await user.click(hours.getByRole('radio', { name: 'Teile' }))
    await user.click(hours.getByRole('checkbox', { name: 'Montag' }))
    await user.click(hours.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/opening-hours$/, 'PUT')).toHaveLength(1))
    const body = backend.callsTo(/\/opening-hours$/, 'PUT')[0].body as { perDepartment: boolean; hours: { department: string }[] }
    expect(body.perDepartment).toBe(true)
    expect(body.hours.map((h) => h.department)).toEqual(['sales', 'service'])
  })

  it('refuses hours that cannot be true before sending them', async () => {
    const backend = installFakeBackend(routes())
    const user = userEvent.setup()
    renderDetail()
    const hours = within(await screen.findByRole('region', { name: 'Öffnungszeiten' }))
    await waitFor(() => expect(hours.getByRole('checkbox', { name: 'Montag' })).toBeChecked())
    const closes = hours.getByLabelText('Montag Bis')
    await user.clear(closes)
    await user.type(closes, '06:00')
    await user.click(hours.getByRole('button', { name: 'Speichern' }))
    expect(await hours.findByRole('alert')).toHaveTextContent('Prüfen Sie die Zeiten von Montag')
    expect(backend.callsTo(/\/opening-hours$/, 'PUT')).toHaveLength(0)
  })

  it('adds a closure with an Idempotency-Key', async () => {
    const backend = installFakeBackend(
      routes([
        {
          method: 'POST',
          match: /\/locations\/loc-1\/closures$/,
          handler: (req) => ({ id: 'cl-2', locationId: 'loc-1', version: 1, createdAt: '', updatedAt: '', createdBy: null, updatedBy: null, ...(req.body as object) }),
        },
      ]),
    )
    const user = userEvent.setup()
    renderDetail()
    await user.click(await screen.findByRole('button', { name: 'Schliessung hinzufügen' }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/^Von/), '2026-12-24')
    await user.type(dialog.getByLabelText(/^Bis/), '2026-12-24')
    await user.type(dialog.getByRole('textbox', { name: (n) => n.startsWith('Grund') }), 'Heiligabend')
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/closures$/, 'POST')).toHaveLength(1))
    const call = backend.callsTo(/\/closures$/, 'POST')[0]
    expect(call.body).toEqual({ startsOn: '2026-12-24', endsOn: '2026-12-24', reason: 'Heiligabend' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('says the last active location cannot be deactivated (FR-A-03)', async () => {
    installFakeBackend(routes())
    renderDetail()
    // Zürich Nord is the only active site of the two.
    const primary = await screen.findByRole('button', { name: 'Deaktivieren' })
    await waitFor(() => expect(primary).toBeDisabled())
  })

  it('reads only, for a role without the write', async () => {
    installFakeBackend(routes([READ_ONLY_SESSION]))
    renderDetail()
    expect(await screen.findByRole('heading', { name: 'Stammdaten' })).toBeInTheDocument()
    expect(screen.getByText('Nur ein Manager dieses Betriebs ändert dessen Standorte.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Deaktivieren' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Schliessung hinzufügen' })).toBeNull()
  })
})

// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { BusinessPage } from './BusinessPage'

// KAN-203 — Dealer Administration › Business: the sections (five, and since
// KAN-37 the Address entry switch) render from
// the dealership, its settings and its bank accounts; the Banking section is
// absent for a role the API answers 404 to (ADR-100); a change of the legal
// name shows the confirmation and the save carries `confirmation: true`
// with the record's If-Match; a settings save sends only what changed.

const DEALERSHIP = {
  id: 'dealership-1',
  dealerGroupId: 'group-1',
  legalName: 'Test Motors AG',
  tradeName: 'Garage Test',
  franchiseType: 'independent',
  oemAffiliations: null,
  address: { street: 'Bahnhofstrasse', houseNumber: '1', postalCode: '8001', locality: 'Zürich', canton: 'ZH', country: 'CH' },
  correspondenceAddress: null,
  billingAddress: null,
  phone: '+41441234567',
  uid: 'CHE-123.456.788',
  vatNumber: 'CHE-123.456.788 MWST',
  vatLiable: true,
  status: 'active',
  vatRate: '8.10',
  ageingAlertThresholds: null,
  legalForm: 'ag',
  commercialRegisterOffice: 'ZH',
  hrNumber: 'CH-020.3.012.345-6',
  astraOperatorNumber: null,
  collectiveVehicleLicences: [{ plateNumber: 'ZH 12345 U', canton: 'ZH', expiresOn: '2027-03-31' }],
  associationMemberships: ['agvs_upsa'],
  version: 3,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  createdBy: null,
  updatedBy: null,
}

const SETTINGS = {
  dealershipId: 'dealership-1',
  currency: 'CHF',
  cashRounding: '0.05',
  defaultPaymentTermsDays: 30,
  fiscalYearStart: '01-01',
  accountingSystem: null,
  mandant: null,
  chartOfAccountsVariant: 'kmu',
  periodLockPolicy: 'month_end_close',
  addressAutocompleteEnabled: true,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  updatedBy: null,
}

const account = (overrides: Record<string, unknown>) => ({
  id: 'acc-1',
  dealershipId: 'dealership-1',
  accountHolder: 'Test Motors AG',
  bankName: 'Zürcher Kantonalbank',
  iban: 'CH9300762011623852957',
  bic: null,
  qrIban: null,
  qrReferenceType: 'non',
  creditorReferencePrefix: null,
  label: 'Hauptkonto',
  isDefault: true,
  version: 1,
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
  createdBy: null,
  updatedBy: null,
  ...overrides,
})

const list = (code: string, rows: [string, string][]) => ({
  method: 'GET',
  match: new RegExp(`/reference-data/${code}$`),
  handler: () => ({
    items: rows.map(([valueCode, labelDe], i) => ({
      id: `${code}-${valueCode}`,
      listCode: code,
      valueCode,
      labelDe,
      labelFr: labelDe,
      labelIt: labelDe,
      labelEn: labelDe,
      sortOrder: i,
      active: true,
      version: 1,
      createdAt: '2026-01-01T00:00:00Z',
      updatedAt: '2026-01-01T00:00:00Z',
      createdBy: null,
      updatedBy: null,
    })),
    nextCursor: null,
  }),
})

function routes(extra: FakeRoute[] = []): FakeRoute[] {
  return [
    ...extra,
    { method: 'GET', match: /\/dealerships\/dealership-1$/, handler: () => DEALERSHIP },
    { method: 'GET', match: /\/dealerships\/dealership-1\/settings$/, handler: () => SETTINGS },
    {
      method: 'GET',
      match: /\/dealerships\/dealership-1\/bank-accounts$/,
      handler: () => ({ items: [account({}), account({ id: 'acc-2', iban: 'DE89370400440532013000', label: 'Werkstatt', isDefault: false, bankName: 'Commerzbank' })] }),
    },
    list('legal_form', [['ag', 'Aktiengesellschaft (AG)'], ['gmbh', 'GmbH']]),
    list('oem_affiliations', [['vw', 'Volkswagen']]),
    list('association_membership', [['agvs_upsa', 'AGVS – Auto Gewerbe Verband Schweiz']]),
  ]
}

describe('BusinessPage (KAN-203)', () => {
  it('renders the six sections from the dealership, its settings and its bank accounts', async () => {
    installFakeBackend(routes())
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })

    expect(await screen.findByRole('heading', { name: 'Rechtliche Identität' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Adressen' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Adresseingabe' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Bankverbindungen' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Kaufmännische Vorgaben' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Bewilligungen und Mitgliedschaften' })).toBeInTheDocument()

    expect(screen.getByDisplayValue('Test Motors AG')).toBeInTheDocument()
    expect(screen.getByDisplayValue('CHE-123.456.788')).toBeInTheDocument()
    // The IBAN prints as a bank prints it, the invoice account is marked.
    const banking = within(screen.getByRole('region', { name: 'Bankverbindungen' }))
    expect(banking.getByText('CH93 0076 2011 6238 5295 7')).toBeInTheDocument()
    expect(banking.getByText('DE89 3704 0044 0532 0130 00')).toBeInTheDocument()
    expect(banking.getAllByText('Rechnungskonto')).toHaveLength(1)
    // The shipped fiscal defaults are on screen.
    expect(screen.getByDisplayValue('CHF')).toBeInTheDocument()
    expect(screen.getByDisplayValue('0.05 CHF')).toBeInTheDocument()
    expect(screen.getByDisplayValue('30 Tage')).toBeInTheDocument()
    // The Händlerschild row.
    expect(screen.getByDisplayValue('ZH 12345 U')).toBeInTheDocument()
  })

  it('shows no Banking section at all when the API answers 404 (ADR-100)', async () => {
    installFakeBackend(routes([{ method: 'GET', match: /\/bank-accounts$/, handler: () => status(404, { error: { code: 'not_found', message: 'Not found.', details: null } }) }]))
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })

    expect(await screen.findByRole('heading', { name: 'Kaufmännische Vorgaben' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Bankverbindungen' })).toBeNull())
    expect(screen.queryByRole('region', { name: 'Bankverbindungen' })).toBeNull()
    expect(screen.queryByText('Konto hinzufügen')).toBeNull()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('asks for the confirmation on a legal-name change and sends it with If-Match', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PATCH', match: /\/dealerships\/dealership-1$/, handler: (req) => ({ ...DEALERSHIP, ...(req.body as object), version: 4 }) }]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const legal = within(await screen.findByRole('region', { name: 'Rechtliche Identität' }))

    // A trade-name change alone needs no confirmation.
    expect(legal.queryByRole('checkbox', { name: /Handelsregister entsprechen/ })).toBeNull()
    const name = legal.getByDisplayValue('Test Motors AG')
    await user.clear(name)
    await user.type(name, 'Neue Motors AG')
    const confirm = await legal.findByRole('checkbox', { name: /Handelsregister entsprechen/ })
    const save = legal.getByRole('button', { name: 'Speichern' })
    expect(save).toBeDisabled()
    await user.click(confirm)
    await user.type(legal.getByLabelText('Grund (optional)'), 'Umfirmierung')
    await user.click(save)

    await waitFor(() => expect(backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')).toHaveLength(1))
    const call = backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')[0]
    expect(call.body).toEqual({ legalName: 'Neue Motors AG', confirmation: true, auditNote: 'Umfirmierung' })
    expect(call.headers.get('If-Match')).toBe('3')
    expect(await legal.findByRole('status')).toHaveTextContent('Gespeichert.')
  })

  it('saves only the changed fiscal default, with the settings version as If-Match', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PATCH', match: /\/dealerships\/dealership-1\/settings$/, handler: (req) => ({ ...SETTINGS, ...(req.body as object), version: 2 }) }]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const fiscal = within(await screen.findByRole('region', { name: 'Kaufmännische Vorgaben' }))

    await user.click(fiscal.getByDisplayValue('0.05 CHF'))
    await user.click(await screen.findByRole('option', { name: '0.10 CHF' }))
    await user.click(fiscal.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/settings$/, 'PATCH')).toHaveLength(1))
    const call = backend.callsTo(/\/settings$/, 'PATCH')[0]
    expect(call.body).toEqual({ cashRounding: '0.10' })
    expect(call.headers.get('If-Match')).toBe('1')
    expect(backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')).toHaveLength(0)
  })

  it('a section saved after another section carries the new version, and a double fiscal save too', async () => {
    // A fake server that enforces If-Match: a stale version is a 409.
    let dealershipVersion = 3
    let settingsVersion = 1
    const backend = installFakeBackend(
      routes([
        {
          method: 'PATCH',
          match: /\/dealerships\/dealership-1$/,
          // Held in flight (KAN-288's pattern): the network takes time, so the
          // cache delivers the saved settings while the VAT PATCH is still out.
          handler: async (req) => {
            await new Promise((resolve) => setTimeout(resolve, 30))
            if (req.headers.get('If-Match') !== String(dealershipVersion)) return status(409, { error: { code: 'conflict', message: 'stale', details: null } })
            dealershipVersion += 1
            return { ...DEALERSHIP, ...(req.body as object), version: dealershipVersion }
          },
        },
        {
          method: 'PATCH',
          match: /\/dealerships\/dealership-1\/settings$/,
          handler: (req) => {
            if (req.headers.get('If-Match') !== String(settingsVersion)) return status(409, { error: { code: 'conflict', message: 'stale', details: null } })
            settingsVersion += 1
            return { ...SETTINGS, ...(req.body as object), version: settingsVersion }
          },
        },
      ]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const legal = within(await screen.findByRole('region', { name: 'Rechtliche Identität' }))
    const addresses = within(await screen.findByRole('region', { name: 'Adressen' }))
    const fiscal = within(await screen.findByRole('region', { name: 'Kaufmännische Vorgaben' }))

    // Legal is dirty (a trade name) while Addresses saves first.
    await user.clear(legal.getByLabelText(/Geschäftsbezeichnung/))
    await user.type(legal.getByLabelText(/Geschäftsbezeichnung/), 'Garage Test 2')
    await user.clear(addresses.getByLabelText(/Telefon/))
    await user.type(addresses.getByLabelText(/Telefon/), '+41447654321')
    await user.click(addresses.getByRole('button', { name: 'Speichern' }))
    expect(await addresses.findByRole('status')).toHaveTextContent('Gespeichert.')  // the held response arrived
    await user.click(legal.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')).toHaveLength(2))
    const second = backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')[1]
    expect(second.headers.get('If-Match')).toBe('4')
    expect(second.body).toEqual({ tradeName: 'Garage Test 2' })
    expect(await legal.findByRole('status')).toHaveTextContent('Gespeichert.')
    expect(legal.queryByRole('alert')).toBeNull()

    // Fiscal: a setting and the VAT rate together, then another setting.
    await user.click(fiscal.getByDisplayValue('0.05 CHF'))
    await user.click(await screen.findByRole('option', { name: '0.10 CHF' }))
    await user.type(fiscal.getByLabelText(/MWST-Satz/), '8.2')
    await user.click(fiscal.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/settings$/, 'PATCH')).toHaveLength(1))
    await waitFor(() => expect(backend.callsTo(/\/dealerships\/dealership-1$/, 'PATCH')).toHaveLength(3))
    await user.click(fiscal.getByDisplayValue('0.10 CHF'))
    await user.click(await screen.findByRole('option', { name: '1.00 CHF' }))
    await user.click(fiscal.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/settings$/, 'PATCH')).toHaveLength(2))
    const settingsCalls = backend.callsTo(/\/settings$/, 'PATCH')
    expect(settingsCalls[0].headers.get('If-Match')).toBe('1')
    expect(settingsCalls[1].headers.get('If-Match')).toBe('2')
    expect(settingsCalls[1].body).toEqual({ cashRounding: '1.00' })
    expect(fiscal.queryByRole('alert')).toBeNull()
  })

  it('the bank-account dialog drops a QR-IBAN once the reference type no longer takes it, and a 422 names the field', async () => {
    const backend = installFakeBackend(
      routes([
        {
          method: 'POST',
          match: /\/bank-accounts$/,
          handler: () =>
            status(422, {
              error: { code: 'unprocessable_entity', message: 'Validation failed', details: { errors: [{ loc: ['body', 'iban'], msg: 'IBAN is not valid', type: 'value_error' }] } },
            }),
        },
      ]),
    )
    const user = userEvent.setup()
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    await screen.findByRole('region', { name: 'Bankverbindungen' })
    await user.click(screen.getByRole('button', { name: 'Konto hinzufügen' }))
    const dialog = within(await screen.findByRole('dialog'))
    await user.type(dialog.getByLabelText(/^Kontoinhaber/), 'Garage Test AG')
    await user.type(dialog.getByLabelText(/^Bank/), 'ZKB')
    await user.type(dialog.getByLabelText(/^IBAN/), 'CH93 0076 2011 6238 5295 8')
    await user.click(dialog.getByRole('textbox', { name: /Referenz auf der QR-Rechnung/ }))
    await user.click(await screen.findByRole('option', { name: 'QR-Referenz (QRR)' }))
    await user.type(dialog.getByLabelText(/^QR-IBAN/), 'CH44 3199 9123 0008 8901 2')
    await user.click(dialog.getByRole('textbox', { name: /Referenz auf der QR-Rechnung/ }))
    await user.click(await screen.findByRole('option', { name: 'Ohne Referenz' }))
    expect(dialog.queryByLabelText(/^QR-IBAN/)).toBeNull()
    await user.click(dialog.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/bank-accounts$/, 'POST')).toHaveLength(1))
    const body = backend.callsTo(/\/bank-accounts$/, 'POST')[0].body as Record<string, unknown>
    expect(body.qrIban).toBeNull()
    expect(body.qrReferenceType).toBe('non')
    expect(await dialog.findByRole('alert')).toHaveTextContent('Bitte prüfen Sie das Feld «IBAN».')
  })

  it("a banking write refused with 404 is said as the manager's, not as a failed request", async () => {
    installFakeBackend(routes([{ method: 'PATCH', match: /\/bank-accounts\/acc-2$/, handler: () => status(404, { error: { code: 'not_found', message: 'Not found.', details: null } }) }]))
    const user = userEvent.setup()
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const banking = within(await screen.findByRole('region', { name: 'Bankverbindungen' }))
    const menus = banking.getAllByRole('button', { name: 'Zeilenaktionen' })
    await user.click(menus[1])
    await user.click(await screen.findByRole('menuitem', { name: 'Als Rechnungskonto festlegen' }))
    expect(await banking.findByRole('alert')).toHaveTextContent('Nur ein Manager dieses Betriebs ändert die Bankverbindungen.')
  })

  // KAN-37 (ADR-101): the Betrieb's switch for Google Places Autocomplete,
  // on by default (Anto, 2026-10-25), saved with the settings version as
  // If-Match; while the platform holds no key the section says so.
  it('the address-entry section turns the autocomplete off with the settings If-Match, and names a missing platform key', async () => {
    const backend = installFakeBackend(
      routes([{ method: 'PATCH', match: /\/dealerships\/dealership-1\/settings$/, handler: (req) => ({ ...SETTINGS, ...(req.body as object), version: 2 }) }]),
    )
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const user = userEvent.setup()

    const section = within(await screen.findByRole('region', { name: 'Adresseingabe' }))
    // The fake backend's config read answers "off" (no key): the switch is on, the note explains.
    expect(await section.findByRole('note')).toHaveTextContent('kein Google-Schlüssel')
    const toggle = section.getByRole('switch', { name: (n) => n.startsWith('Adressvorschläge von Google Places') })
    expect(toggle).toBeChecked()
    expect(section.getByRole('button', { name: 'Speichern' })).toBeDisabled()

    await user.click(toggle)
    await user.click(section.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(backend.callsTo(/\/settings$/, 'PATCH')).toHaveLength(1))
    const call = backend.callsTo(/\/settings$/, 'PATCH')[0]
    expect(call.body).toEqual({ addressAutocompleteEnabled: false })
    expect(call.headers.get('If-Match')).toBe('1')
    expect(await section.findByRole('status')).toHaveTextContent('Gespeichert.')
    expect(section.queryByRole('note')).toBeNull()
  })

  it('a failed config read says nothing about a missing key', async () => {
    installFakeBackend(routes([{ method: 'GET', match: /\/address-autocomplete$/, handler: () => status(500, { error: { code: 'internal', message: 'boom', details: null } }) }]))
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })
    const section = within(await screen.findByRole('region', { name: 'Adresseingabe' }))
    expect(section.getByRole('switch', { name: (n) => n.startsWith('Adressvorschläge von Google Places') })).toBeChecked()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Bankverbindungen' })).toBeInTheDocument())
    expect(section.queryByRole('note')).toBeNull()
  })
})

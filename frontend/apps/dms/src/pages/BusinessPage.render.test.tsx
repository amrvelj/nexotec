// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../test/fakeBackend'
import { BusinessPage } from './BusinessPage'

// KAN-203 — Dealer Administration › Business: the five sections render from
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
  it('renders the five sections from the dealership, its settings and its bank accounts', async () => {
    installFakeBackend(routes())
    renderWithProviders(<BusinessPage />, { route: '/administration/business' })

    expect(await screen.findByRole('heading', { name: 'Rechtliche Identität' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Adressen' })).toBeInTheDocument()
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
})

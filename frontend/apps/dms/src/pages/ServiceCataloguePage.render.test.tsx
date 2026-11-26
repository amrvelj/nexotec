// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import {
  ALL_ENTITLEMENTS,
  ALL_GATED_CAPABILITIES,
  installFakeBackend,
  type FakeRoute,
  type RecordedCall,
} from '../test/fakeBackend'
import { ServiceCataloguePage } from './ServiceCataloguePage'

// KAN-818 — Dealer Administration › Service catalogue (S1; section 3.10, rulings 176, 182, 183).

const BASE = '/dealerships/dealership-1'
const PACKAGES = `${BASE}/aftersales/service-packages`

const labour = (id: string, quantity: string, unit: 'hours' | 'work_units' = 'hours', checkNeeded = false) => ({
  id,
  sortIndex: 1,
  kind: 'labour',
  text: 'Service nach Herstellervorgabe',
  labourCategoryId: 'cat-1',
  labourCategoryLabel: 'Mechanik',
  time: { quantity, unit, workUnitsPerHour: unit === 'work_units' ? '12.00' : null },
  checkNeeded,
})

const SERVICE = {
  id: 'pkg-1',
  // Typed in French; the UI language (German) has its translation.
  name: 'Service 30 000 km',
  nameTranslations: { de: 'Service 30’000 km' },
  isActive: true,
  positions: [
    labour('pos-1', '1.50', 'hours', true),
    { id: 'pos-2', sortIndex: 2, kind: 'text', text: 'Ölfilter', labourCategoryId: null, labourCategoryLabel: null, time: null, checkNeeded: false },
  ],
  version: 3,
  updatedAt: '2026-11-26T08:00:00Z',
}
const BRAKES = {
  id: 'pkg-2',
  name: 'Bremsenservice',
  nameTranslations: {},
  isActive: false,
  positions: [labour('pos-3', '0.75')],
  version: 1,
  updatedAt: '2026-11-26T08:00:00Z',
}

const CATEGORIES = [
  { id: 'cat-1', name: 'Mechanik', nameTranslations: null, isActive: true, version: 1, rates: [], members: [] },
  { id: 'cat-2', name: 'Elektrik', nameTranslations: { de: 'Elektrik' }, isActive: true, version: 1, rates: [], members: [] },
]

const declaration = (key: string, kind: string) => ({
  key,
  area: 'labour_rates',
  module: 'aftersales',
  kind,
  whenNotSet: 'quiet',
  actsAs: null,
  minimum: null,
  maximum: null,
  decimals: 0,
  unit: null,
  choices: [],
  checklistItem: false,
  checklistBlocks: null,
  choiceRequires: [],
  divides: null,
  multipleOf: null,
})
const slot = (value: Record<string, unknown> | null) => ({
  locationId: null,
  isSet: value !== null,
  value,
  version: value ? 1 : 0,
  changedAt: null,
  changedBy: null,
  fitsDeclaration: true,
})
const settings = (workUnits: boolean) => ({
  area: 'labour_rates',
  showsLocations: false,
  locations: [{ id: 'loc-1', name: 'Hauptsitz', isActive: true }],
  settings: [
    {
      declaration: declaration('labour_rates.time_unit', 'choice'),
      dealership: slot(workUnits ? { choice: 'work_units' } : null),
      locations: [],
    },
    {
      declaration: declaration('labour_rates.work_units_per_hour', 'number'),
      dealership: slot(workUnits ? { number: '12' } : null),
      locations: [],
    },
  ],
})

function install(options: { canWrite?: boolean; workUnits?: boolean; extra?: FakeRoute[] } = {}) {
  const canWrite = options.canWrite ?? true
  return installFakeBackend([
    ...(options.extra ?? []),
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: { id: 'user-1', firstName: 'Anna', lastName: 'Muster', email: 'anna@example.ch' },
        activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
        memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
        entitlements: ALL_ENTITLEMENTS,
        // The desk reads the catalogue; only the manager writes it.
        writableCapabilities: canWrite
          ? ALL_GATED_CAPABILITIES
          : ALL_GATED_CAPABILITIES.filter((capability) => capability !== 'service_packages'),
      }),
    },
    { method: 'GET', match: new RegExp(`${PACKAGES}$`), handler: () => ({ items: [SERVICE, BRAKES] }) },
    { method: 'GET', match: new RegExp(`${BASE}/labour-rate-categories$`), handler: () => ({ items: CATEGORIES }) },
    {
      method: 'GET',
      match: new RegExp(`${BASE}/setting-areas/labour_rates$`),
      handler: () => settings(options.workUnits ?? false),
    },
  ])
}

afterEach(() => {
  cleanup()
})

const render = () => renderWithProviders(<ServiceCataloguePage />, { route: '/administration/service-catalogue' })
const table = () => screen.findByRole('table', { name: i18n.t('serviceCatalogue.title') })
const calls = (backend: { calls: RecordedCall[] }, method: string, path: RegExp) =>
  backend.calls.filter((call) => call.method === method && path.test(call.pathname))

describe('ServiceCataloguePage (KAN-818)', () => {
  it('lists every package in the UI language, as typed without a translation, with time, check and state', async () => {
    install()
    render()
    const packages = await table()
    // Check 46 / ruling 176: the German name, not the typed French one …
    const service = within(packages).getByRole('row', { name: (n) => n.includes('Service 30’000 km') })
    expect(within(packages).queryByText('Service 30 000 km')).toBeNull()
    // … and a package without a translation as typed.
    const brakes = within(packages).getByRole('row', { name: (n) => n.includes('Bremsenservice') })
    expect(within(service).getByText('1.50 h')).toBeInTheDocument()
    expect(within(service).getByText(i18n.t('serviceCatalogue.checkBadge'))).toBeInTheDocument()
    expect(within(service).getByText(i18n.t('serviceCatalogue.status.active'))).toBeInTheDocument()
    // An inactive package stays listed (check 45: not offered on a job, kept here).
    expect(within(brakes).getByText(i18n.t('serviceCatalogue.status.inactive'))).toBeInTheDocument()
    expect(within(brakes).getByRole('button', { name: i18n.t('serviceCatalogue.activateNamed', { name: 'Bremsenservice' }) })).toBeEnabled()
    expect(screen.getByText(i18n.t('serviceCatalogue.unitInForce.hours'))).toBeInTheDocument()
  })

  it('offers no write to the desk', async () => {
    install({ canWrite: false })
    render()
    await table()
    expect(screen.queryByRole('button', { name: i18n.t('serviceCatalogue.add') })).toBeNull()
    expect(screen.queryByRole('button', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.edit')) })).toBeNull()
    expect(screen.getByText(i18n.t('serviceCatalogue.readOnly'))).toBeInTheDocument()
  })

  it('creates a package with a labour position in work units, «Prüfung nötig» and a text position', async () => {
    const backend = install({
      workUnits: true,
      extra: [{ method: 'POST', match: new RegExp(`${PACKAGES}$`), handler: () => ({ ...BRAKES, id: 'pkg-3' }) }],
    })
    render()
    await table()
    expect(screen.getByText(i18n.t('serviceCatalogue.unitInForce.work_units', { perHour: '12' }))).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('serviceCatalogue.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('serviceCatalogue.dialog.createTitle') })
    const save = within(dialog).getByRole('button', { name: i18n.t('common.save') })
    expect(save).toBeDisabled() // no name yet
    const [name] = within(dialog).getAllByRole('textbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.name')) })
    await userEvent.type(name, 'Klimaservice')
    await userEvent.type(
      within(dialog).getByRole('textbox', {
        name: i18n.t('serviceCatalogue.dialog.translation', { language: i18n.t('serviceCatalogue.languages.fr') }),
      }),
      'Service climatisation',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('serviceCatalogue.dialog.addLabour') }))
    const first = within(dialog).getByLabelText(i18n.t('serviceCatalogue.dialog.positionLabel', { position: 1 }))
    await userEvent.type(within(first).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.text')) }), 'Klimaanlage prüfen')
    expect(save).toBeDisabled() // a labour position without category and time
    await userEvent.click(within(first).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.category')) }))
    await userEvent.click(await screen.findByRole('option', { name: 'Mechanik' }))
    await userEvent.type(within(first).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.time')) }), '9')
    expect(within(first).getByText(i18n.t('serviceCatalogue.unitShort.work_units'))).toBeInTheDocument()
    await userEvent.click(within(first).getByRole('checkbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.checkNeeded')) }))
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('serviceCatalogue.dialog.addText') }))
    const second = within(dialog).getByLabelText(i18n.t('serviceCatalogue.dialog.positionLabel', { position: 2 }))
    await userEvent.type(within(second).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('serviceCatalogue.dialog.text')) }), 'Kältemittel nach Bedarf')
    await userEvent.click(save)

    await waitFor(() => expect(calls(backend, 'POST', new RegExp(`${PACKAGES}$`))).toHaveLength(1))
    expect(calls(backend, 'POST', new RegExp(`${PACKAGES}$`))[0].body).toEqual({
      name: 'Klimaservice',
      nameTranslations: { fr: 'Service climatisation' },
      isActive: true,
      positions: [
        {
          id: null,
          kind: 'labour',
          text: 'Klimaanlage prüfen',
          labourCategoryId: 'cat-1',
          // A decimal string (ADR-217), in the unit in force (ruling 183).
          time: '9.00',
          timeUnit: 'work_units',
          checkNeeded: true,
        },
        { id: null, kind: 'text', text: 'Kältemittel nach Bedarf', checkNeeded: false },
      ],
    })
  })

  it('deactivates a package by sending it back whole with If-Match', async () => {
    const backend = install({
      extra: [{ method: 'PUT', match: new RegExp(`${PACKAGES}/pkg-1$`), handler: () => ({ ...SERVICE, isActive: false, version: 4 }) }],
    })
    render()
    const packages = await table()
    await userEvent.click(
      within(packages).getByRole('button', { name: i18n.t('serviceCatalogue.deactivateNamed', { name: 'Service 30’000 km' }) }),
    )
    await waitFor(() => expect(calls(backend, 'PUT', /\/service-packages\/pkg-1$/)).toHaveLength(1))
    const put = calls(backend, 'PUT', /\/service-packages\/pkg-1$/)[0]
    expect(put.headers.get('If-Match')).toBe('3')
    expect(put.body).toMatchObject({
      name: 'Service 30 000 km',
      isActive: false,
      positions: [
        { id: 'pos-1', kind: 'labour', time: '1.50', timeUnit: 'hours', checkNeeded: true },
        { id: 'pos-2', kind: 'text', text: 'Ölfilter' },
      ],
    })
  })
})

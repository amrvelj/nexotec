// @vitest-environment jsdom
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, type FakeRoute, type RecordedCall } from '../test/fakeBackend'
import { LabourRatesPage } from './LabourRatesPage'

// KAN-806 — Dealer Administration › Labour rates (D-A-19; rulings 177, 178, 183).

const BASE = /^\/dealerships\/dealership-1/

const slot = (value: unknown, version = 0) => ({
  locationId: null,
  isSet: value !== null,
  value,
  version,
  changedAt: null,
  changedBy: null,
  fitsDeclaration: true,
})

const declaration = (key: string, kind: string, extra: Record<string, unknown> = {}) => ({
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
  ...extra,
})

function settings({
  entered = null as string | null,
  perHour = null as string | null,
  locationEntered = null as string | null,
} = {}) {
  return {
    area: 'labour_rates',
    showsLocations: false,
    locations: [],
    settings: [
      {
        declaration: declaration('labour_rates.rates_entered', 'choice', { choices: ['net', 'gross'] }),
        dealership: slot(entered === null ? null : { choice: entered }, entered === null ? 0 : 1),
        locations:
          locationEntered === null ? [] : [{ ...slot({ choice: locationEntered }, 1), locationId: 'loc-1' }],
      },
      {
        declaration: declaration('labour_rates.time_unit', 'choice', {
          choices: ['hours', 'work_units'],
          choiceRequires: [{ choice: 'work_units', key: 'labour_rates.work_units_per_hour' }],
        }),
        dealership: slot(null),
        locations: [],
      },
      {
        declaration: declaration('labour_rates.work_units_per_hour', 'number'),
        dealership: slot(perHour === null ? null : { number: perHour }, perHour === null ? 0 : 1),
        locations: [],
      },
    ],
  }
}

const rate = (id: string, status: string, validFrom: string, amount = '150.0000') => ({
  id,
  locationId: 'loc-1',
  validFrom,
  amount,
  entered: 'net',
  status,
  withdrawnAt: null,
  version: 1,
  createdAt: '2026-11-25T08:00:00Z',
  createdBy: null,
})

const CATEGORY = {
  id: 'cat-1',
  name: 'Mechanik',
  nameTranslations: {},
  isActive: true,
  version: 1,
  createdAt: '2026-11-25T08:00:00Z',
  rates: [rate('rate-2', 'scheduled', '2026-12-01', '160.0000'), rate('rate-1', 'in_force', '2026-11-25')],
  members: [{ userId: 'user-9', displayName: 'Lea Meier', isActive: true }],
}

function install(
  options: {
    entered?: string | null
    perHour?: string | null
    locationEntered?: string | null
    extra?: FakeRoute[]
  } = {},
) {
  return installFakeBackend([
    ...(options.extra ?? []),
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/labour-rate-categories$`),
      handler: () => ({ today: '2026-11-25', items: [CATEGORY] }),
    },
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/setting-areas/labour_rates$`),
      handler: () =>
        settings({
          entered: options.entered ?? null,
          perHour: options.perHour ?? null,
          locationEntered: options.locationEntered ?? null,
        }),
    },
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/locations$`),
      handler: () => ({ items: [{ id: 'loc-1', name: 'Hauptsitz', isActive: true }] }),
    },
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/labour-rate-agreements$`),
      handler: () => ({ today: '2026-11-25', items: [], nextCursor: null }),
    },
    {
      method: 'GET',
      match: new RegExp(`${BASE.source}/labour-category-candidates$`),
      handler: () => ({ items: [{ userId: 'user-9', displayName: 'Lea Meier' }] }),
    },
  ])
}

afterEach(() => {
  cleanup()
})

const ratesTable = () => screen.findByRole('table', { name: i18n.t('labourRates.rates.title', { name: 'Mechanik' }) })

describe('LabourRatesPage (KAN-806)', () => {
  // A rate's validFrom is a calendar day (formatDateOnly): it must read the same far west of Zurich.
  let tz: string | undefined
  beforeAll(() => {
    tz = process.env.TZ
    process.env.TZ = 'Pacific/Honolulu'
  })
  afterAll(() => {
    if (tz === undefined) delete process.env.TZ
    else process.env.TZ = tz
  })

  it('shows each rate with its status; only a rate still to come can be withdrawn', async () => {
    install({ entered: 'net' })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    const table = await ratesTable()
    const scheduled = within(table).getByRole('row', { name: (n) => n.includes('01.12.2026') })
    const inForce = within(table).getByRole('row', { name: (n) => n.includes('25.11.2026') })
    expect(within(scheduled).getByText(i18n.t('labourRates.status.scheduled'))).toBeInTheDocument()
    expect(within(scheduled).getByText("CHF 160.00 netto")).toBeInTheDocument()
    expect(within(scheduled).getByRole('button', { name: i18n.t('labourRates.withdraw.action') })).toBeInTheDocument()
    // Ruling 178: a rate in use is never changed — no withdraw on it.
    expect(within(inForce).queryByRole('button', { name: i18n.t('labourRates.withdraw.action') })).toBeNull()
  })

  it('without O16 says so and offers no rate entry', async () => {
    install({ entered: null })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    expect(await screen.findByText(i18n.t('labourRates.settings.enteredNotSet'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('labourRates.rates.add') })).toBeDisabled()
  })

  it('enters a new rate from a date, typed as O16 says', async () => {
    const backend = install({
      entered: 'net',
      extra: [
        {
          method: 'POST',
          match: new RegExp(`${BASE.source}/labour-rate-categories/cat-1/rates$`),
          handler: () => CATEGORY,
        },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    await ratesTable()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('labourRates.rates.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('labourRates.rateDialog.title', { name: 'Mechanik' }) })
    expect(within(dialog).getByText(i18n.t('labourRates.rateDialog.writtenOnce'))).toBeInTheDocument()
    await userEvent.type(
      within(dialog).getByRole('textbox', {
        name: (n) => n.startsWith(i18n.t('labourRates.rateDialog.amount', { entered: 'netto' })),
      }),
      '155',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(backend.calls.some((call: RecordedCall) => call.method === 'POST')).toBe(true))
    const post = backend.calls.find((call: RecordedCall) => call.method === 'POST')!
    expect(post.body).toEqual({ locationId: 'loc-1', validFrom: '2026-11-25', amount: '155.00', entered: 'net' })
  })

  it('lists the people of a category', async () => {
    install({ entered: 'net' })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    const card = await screen.findByRole('article', { name: 'Mechanik' })
    expect(within(card).getByText('Lea Meier')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: i18n.t('labourRates.members.remove', { name: 'Lea Meier' }) })).toBeInTheDocument()
  })

  it('a location with its own O16 enters its rate as that location says (ruling 175)', async () => {
    const backend = install({
      entered: null,
      locationEntered: 'gross',
      extra: [
        {
          method: 'POST',
          match: new RegExp(`${BASE.source}/labour-rate-categories/cat-1/rates$`),
          handler: () => CATEGORY,
        },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    await ratesTable()
    // Only a location has O16: the page says rates go at those locations only.
    expect(screen.getByText(i18n.t('labourRates.settings.enteredAtLocationsOnly'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('labourRates.settings.enteredNotSet'))).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('labourRates.rates.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('labourRates.rateDialog.title', { name: 'Mechanik' }) })
    await userEvent.type(
      within(dialog).getByRole('textbox', {
        name: (n) => n.startsWith(i18n.t('labourRates.rateDialog.amount', { entered: 'inkl. MwSt.' })),
      }),
      '162',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(backend.calls.some((call: RecordedCall) => call.method === 'POST')).toBe(true))
    expect(backend.calls.find((call: RecordedCall) => call.method === 'POST')!.body).toMatchObject({ entered: 'gross' })
  })

  it('renames a category with its version and withdraws a rate still to come with its version', async () => {
    const backend = install({
      entered: 'net',
      extra: [
        { method: 'PATCH', match: new RegExp(`${BASE.source}/labour-rate-categories/cat-1$`), handler: () => CATEGORY },
        { method: 'POST', match: new RegExp(`${BASE.source}/labour-rates/rate-2/withdraw$`), handler: () => CATEGORY },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    const card = await screen.findByRole('article', { name: 'Mechanik' })
    await userEvent.click(within(card).getByRole('button', { name: i18n.t('labourRates.categories.edit') }))
    const edit = await screen.findByRole('dialog', { name: i18n.t('labourRates.categoryDialog.editTitle') })
    // The first «Name» field; the translations follow as «Name auf …».
    const [name] = within(edit).getAllByRole('textbox', { name: (n) => n.startsWith(i18n.t('labourRates.categoryDialog.name')) })
    await userEvent.clear(name)
    await userEvent.type(name, 'Mechanik und Elektrik')
    await userEvent.click(within(edit).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() => expect(backend.calls.some((call: RecordedCall) => call.method === 'PATCH')).toBe(true))
    const patch = backend.calls.find((call: RecordedCall) => call.method === 'PATCH')!
    expect(patch.headers.get('If-Match')).toBe('1')
    expect(patch.body).toMatchObject({ name: 'Mechanik und Elektrik' })

    const scheduled = within(await ratesTable()).getByRole('row', { name: (n) => n.includes('01.12.2026') })
    await userEvent.click(within(scheduled).getByRole('button', { name: i18n.t('labourRates.withdraw.action') }))
    const confirm = await screen.findByRole('dialog', { name: i18n.t('labourRates.withdraw.rateTitle') })
    expect(within(confirm).getByText(i18n.t('labourRates.withdraw.rateBody'))).toBeInTheDocument()
    await userEvent.click(within(confirm).getByRole('button', { name: i18n.t('labourRates.withdraw.action') }))
    await waitFor(() =>
      expect(backend.calls.some((call: RecordedCall) => call.pathname.endsWith('/labour-rates/rate-2/withdraw'))).toBe(true),
    )
    const withdraw = backend.calls.find((call: RecordedCall) => call.pathname.endsWith('/labour-rates/rate-2/withdraw'))!
    expect(withdraw.headers.get('If-Match')).toBe('1')
  })

  it('removes a person from a category', async () => {
    const backend = install({
      entered: 'net',
      extra: [
        {
          method: 'DELETE',
          match: new RegExp(`${BASE.source}/labour-rate-categories/cat-1/members/user-9$`),
          handler: () => null,
        },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    const card = await screen.findByRole('article', { name: 'Mechanik' })
    await userEvent.click(
      within(card).getByRole('button', { name: i18n.t('labourRates.members.remove', { name: 'Lea Meier' }) }),
    )
    await waitFor(() => expect(backend.calls.some((call: RecordedCall) => call.method === 'DELETE')).toBe(true))
  })

  it('enters an agreed rate for a contact picked from the search', async () => {
    const backend = install({
      entered: 'net',
      extra: [
        {
          method: 'GET',
          match: /^\/contacts$/,
          handler: () => ({
            items: [{ id: 'contact-7', contactNumber: 'K-1007', companyName: 'Helvetia Versicherungen AG' }],
            nextCursor: null,
          }),
        },
        { method: 'POST', match: new RegExp(`${BASE.source}/labour-rate-agreements$`), handler: () => ({}) },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    await ratesTable()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('labourRates.agreements.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('labourRates.agreementDialog.title') })
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('labourRates.agreementDialog.chooseContact') }))
    await userEvent.type(await screen.findByPlaceholderText(i18n.t('labourRates.agreementDialog.searchContact')), 'Helv')
    await userEvent.click(await screen.findByText('Helvetia Versicherungen AG'))
    const form = await screen.findByRole('dialog', { name: i18n.t('labourRates.agreementDialog.title') })
    await userEvent.type(
      within(form).getByRole('textbox', {
        name: (n) => n.startsWith(i18n.t('labourRates.rateDialog.amount', { entered: 'netto' })),
      }),
      '128',
    )
    await userEvent.click(within(form).getByRole('button', { name: i18n.t('common.save') }))
    await waitFor(() =>
      expect(backend.calls.some((call: RecordedCall) => call.method === 'POST' && call.pathname.endsWith('/labour-rate-agreements'))).toBe(true),
    )
    const post = backend.calls.find((call: RecordedCall) => call.method === 'POST')!
    expect(post.body).toEqual({
      contactId: 'contact-7',
      categoryId: 'cat-1',
      kind: 'rate',
      validFrom: '2026-11-25',
      amount: '128.00',
      entered: 'net',
    })
  })

  it('pages the agreements by cursor and asks for withdrawn ones too', async () => {
    const agreement = (id: string, contactLabel: string) => ({
      id,
      contactId: `contact-${id}`,
      contactLabel,
      categoryId: 'cat-1',
      kind: 'rate',
      validFrom: '2026-11-25',
      amount: '120.0000',
      entered: 'net',
      status: 'in_force',
      withdrawnAt: null,
      version: 1,
      createdAt: '2026-11-25T08:00:00Z',
    })
    const backend = install({
      entered: 'net',
      extra: [
        {
          method: 'GET',
          match: new RegExp(`${BASE.source}/labour-rate-agreements$`),
          handler: (request) =>
            request.params.get('cursor') === 'page-2'
              ? { today: '2026-11-25', items: [agreement('a2', 'Zweite AG')], nextCursor: null }
              : { today: '2026-11-25', items: [agreement('a1', 'Erste AG')], nextCursor: 'page-2' },
        },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    expect(await screen.findByText('Erste AG')).toBeInTheDocument()
    expect(screen.queryByText('Zweite AG')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('labourRates.agreements.more') }))
    expect(await screen.findByText('Zweite AG')).toBeInTheDocument()
    const first = backend.calls.find((call: RecordedCall) => call.pathname.endsWith('/labour-rate-agreements'))!
    expect(first.params.get('includeWithdrawn')).toBe('true')
  })

  it('an agreement cannot be saved without the Betrieb\'s O16 and the dialog says why', async () => {
    install({
      entered: null,
      locationEntered: 'gross',
      extra: [
        {
          method: 'GET',
          match: /^\/contacts$/,
          handler: () => ({
            items: [{ id: 'contact-7', contactNumber: 'K-1007', companyName: 'Helvetia Versicherungen AG' }],
            nextCursor: null,
          }),
        },
      ],
    })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    await ratesTable()
    await userEvent.click(screen.getByRole('button', { name: i18n.t('labourRates.agreements.add') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('labourRates.agreementDialog.title') })
    expect(within(dialog).getByText(i18n.t('labourRates.settings.enteredNotSet'))).toBeInTheDocument()
    // Everything else filled in — a contact, the category, a date, an amount — so only O16 holds Save back.
    await userEvent.click(within(dialog).getByRole('button', { name: i18n.t('labourRates.agreementDialog.chooseContact') }))
    await userEvent.type(await screen.findByPlaceholderText(i18n.t('labourRates.agreementDialog.searchContact')), 'Helv')
    await userEvent.click(await screen.findByText('Helvetia Versicherungen AG'))
    const form = await screen.findByRole('dialog', { name: i18n.t('labourRates.agreementDialog.title') })
    expect(within(form).getByText('Helvetia Versicherungen AG')).toBeInTheDocument()
    await userEvent.type(
      within(form).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('labourRates.columns.amount')) }),
      '128',
    )
    expect(within(form).getByRole('button', { name: i18n.t('common.save') })).toBeDisabled()
    // The end of an agreement needs no amount and no O16: then it can be saved.
    await userEvent.click(within(form).getByText(i18n.t('labourRates.agreementDialog.kindEnd')))
    expect(within(form).getByRole('button', { name: i18n.t('common.save') })).toBeEnabled()
  })

  it('embeds the settings rows of O16, O18 and O19 (KAN-805 criterion 6)', async () => {
    install({ entered: null, perHour: null })
    renderWithProviders(<LabourRatesPage />, { route: '/administration/labour-rates' })
    const table = await screen.findByRole('table', { name: i18n.t('settings.tableLabel') })
    for (const key of ['rates_entered', 'time_unit', 'work_units_per_hour']) {
      expect(within(table).getByText(i18n.t(`settings.keys.labour_rates.${key}.label`))).toBeInTheDocument()
    }
    // «Not set» as a state of its own, from the shared rows.
    expect(within(table).getAllByText(i18n.t('settings.notSet.label')).length).toBe(3)
  })
})

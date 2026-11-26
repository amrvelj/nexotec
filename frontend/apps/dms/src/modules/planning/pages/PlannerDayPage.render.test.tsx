// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../i18n'
import { renderWithProviders } from '../../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../../../test/fakeBackend'
import { PlannerDayPage } from './PlannerDayPage'

// KAN-809 — PL1/PL2/PL4: one day of a location, a row per person and per
// workplace; the booking dialog (change, release) says every refusal in one
// sentence and lets a warning be overruled, saying it is recorded; the block
// dialog; the empty state that says where to set people and workplaces up.
// Every time is Zurich's: 08:00Z on 1 March 2027 is 09:00 there.

const BASE = '^/dealerships/dealership-1'
const PLANNING = `${BASE}/planning`
const DAY = '2027-03-01'

const resource = (id: string, extra: Record<string, unknown>) => ({
  id, kind: 'person', locationId: 'loc-1', locationLabel: 'Hauptsitz', userId: null, userLabel: null,
  personActsHere: true, name: null, workplaceTypeId: null, technicianTypeId: null, qualificationIds: [],
  workingHours: [], isActive: true, sortOrder: 0, version: 1, createdAt: '2026-11-25T08:00:00Z', ...extra,
})
const LEA = resource('r-lea', {
  userId: 'u-1', userLabel: 'Lea Meier', technicianTypeId: 'tt-1',
  workingHours: [{ weekday: 1, startsAt: '07:30:00', endsAt: '12:00:00', secondStartsAt: '13:00:00', secondEndsAt: '17:00:00' }],
})
const LIFT = resource('r-lift', { kind: 'workplace', name: 'Bühne 1', workplaceTypeId: 'wt-1' })

const booking = (id: string, extra: Record<string, unknown>) => ({
  id, kind: 'work', status: 'booked', startsAt: '2027-03-01T08:00:00Z', endsAt: '2027-03-01T09:30:00Z',
  resources: [
    { resourceId: 'r-lift', kind: 'workplace', label: 'Bühne 1' },
    { resourceId: 'r-lea', kind: 'person', label: 'Lea Meier' },
  ],
  reference: { type: 'aftersales.order', id: 'o-1', label: 'Auftrag 1001' },
  job: { id: 'j-1', label: 'Jahresservice' },
  vehicle: { id: 'v-1', label: 'VW Golf ZH 123456' },
  note: null, replan: null, overruledWarnings: [], version: 2, ...extra,
})
const WORK = booking('b-1', {})
const MARKED = booking('b-2', {
  startsAt: '2027-03-01T12:00:00Z', endsAt: '2027-03-01T13:00:00Z',
  resources: [{ resourceId: 'r-lift', kind: 'workplace', label: 'Bühne 1' }],
  reference: { type: 'aftersales.order', id: 'o-2', label: 'Auftrag 1002' }, job: { id: 'j-2', label: 'Bremsen' },
  replan: { cause: 'blocked', since: '2027-02-28T10:00:00Z', causedByBookingId: 'b-3', causedByResourceId: null },
  overruledWarnings: [{
    code: 'outside_working_hours', resourceId: 'r-lea', againstBookingId: null, change: 'booked',
    bookedStartsAt: '2027-03-01T12:00:00Z', bookedEndsAt: '2027-03-01T13:00:00Z',
    overruledBy: 'u-9', overruledByName: 'Anna Chef', overruledAt: '2027-02-27T09:05:00Z',
  }],
})
const BLOCK = booking('b-3', {
  kind: 'block', startsAt: '2027-03-01T11:00:00Z', endsAt: '2027-03-01T15:00:00Z',
  resources: [{ resourceId: 'r-lift', kind: 'workplace', label: 'Bühne 1' }],
  reference: null, job: null, vehicle: null, note: 'Wartung', version: 1,
})

function install(options: { resources?: unknown[]; bookings?: unknown[]; grid?: number | null; extra?: FakeRoute[] } = {}) {
  return installFakeBackend([
    ...(options.extra ?? []),
    {
      method: 'GET',
      match: /\/auth\/me$/,
      handler: () => ({
        user: { id: 'user-1', firstName: 'Anna', lastName: 'Chef', email: 'anna@example.ch' },
        activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
        memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
        entitlements: ALL_ENTITLEMENTS,
        writableCapabilities: ALL_GATED_CAPABILITIES,
      }),
    },
    { match: new RegExp(`${BASE}/locations$`), handler: () => ({ items: [{ id: 'loc-1', name: 'Hauptsitz', isActive: true }] }) },
    { match: new RegExp(`${PLANNING}/resources$`), handler: () => ({ items: options.resources ?? [LEA, LIFT] }) },
    {
      match: new RegExp(`${PLANNING}/technician-types$`),
      handler: () => ({ items: [{ id: 'tt-1', name: 'Mechatroniker', nameTranslations: { fr: 'Mécatronicien' }, sortOrder: 0, version: 1 }] }),
    },
    {
      match: new RegExp(`${PLANNING}/workplace-types$`),
      handler: () => ({ items: [{ id: 'wt-1', name: 'Hebebühne', nameTranslations: { fr: 'Pont élévateur' }, sortOrder: 0, version: 1 }] }),
    },
    {
      match: new RegExp(`${PLANNING}/day$`),
      handler: () => ({
        date: DAY, locationId: 'loc-1', startsAt: '2027-02-28T23:00:00Z', endsAt: '2027-03-01T23:00:00Z',
        gridMinutes: options.grid === undefined ? 15 : options.grid, bookings: options.bookings ?? [WORK, MARKED, BLOCK],
      }),
    },
  ])
}

afterEach(() => cleanup())

const render = () =>
  renderWithProviders(
    <Routes>
      <Route path="/planner" element={<PlannerDayPage />} />
    </Routes>,
    { route: `/planner?date=${DAY}&location=loc-1` },
  )

const itemNamed = (text: string) => screen.findByRole('button', { name: new RegExp(text) })

describe('PlannerDayPage (KAN-809)', () => {
  it('shows the day with a row per person and per workplace, Zurich times, and «neu einplanen» in words', async () => {
    install()
    render()

    const grid = await screen.findByRole('group', { name: /Montag, 1\. März 2027, Hauptsitz/ })
    expect(within(grid).getByText('Lea Meier')).toBeInTheDocument()
    expect(within(grid).getByText('Mechatroniker')).toBeInTheDocument()
    expect(within(grid).getByText('Bühne 1')).toBeInTheDocument()
    expect(within(grid).getByText('Hebebühne')).toBeInTheDocument()
    // 08:00Z is 09:00 in Zurich in winter; the work shows on both its resources' rows.
    expect(within(grid).getAllByRole('button', { name: /Auftrag 1001 · Jahresservice, 09:00 bis 10:30/ })).toHaveLength(2)
    const marked = within(grid).getByRole('button', { name: /Auftrag 1002.*neu einplanen/ })
    expect(within(marked).getByText(i18n.t('planner.replan'))).toBeInTheDocument()
    expect(within(grid).getByRole('button', { name: /Blockiert · Wartung, 12:00 bis 16:00/ })).toBeInTheDocument()
  })

  it('a refusal is said in one sentence, in the UI language', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [{
        method: 'PATCH',
        match: new RegExp(`${PLANNING}/bookings/b-1$`),
        handler: () => status(409, { error: { code: 'conflict', message: 'Another car stands at the workplace.', details: { reason: 'planning_workplace_taken', bookingIds: ['b-9'], resourceId: 'r-lift' } } }),
      }],
    })
    render()

    await user.click((await screen.findAllByRole('button', { name: /Auftrag 1001/ }))[0])
    const dialog = await screen.findByRole('dialog', { name: i18n.t('planner.booking.title') })
    await user.clear(within(dialog).getByLabelText(new RegExp(`${i18n.t('planner.fields.from')} – ${i18n.t('planner.fields.time')}`)))
    await user.type(within(dialog).getByLabelText(new RegExp(`${i18n.t('planner.fields.from')} – ${i18n.t('planner.fields.time')}`)), '10:00')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.booking.save') }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('apiRefusal.reasons.planning_workplace_taken'))
    expect(alert).not.toHaveTextContent('Another car')
    const [call] = backend.callsTo(/\/bookings\/b-1$/, 'PATCH')
    expect(call.headers.get('If-Match')).toBe('2')
    expect(call.body).toMatchObject({ startsAt: '2027-03-01T10:00:00+01:00', endsAt: '2027-03-01T10:30:00+01:00', confirmedWarnings: [] })
  })

  it('a warning is listed, can be overruled, says it is recorded, and is sent back exactly as shown', async () => {
    const user = userEvent.setup()
    const shown = { code: 'outside_working_hours', resourceId: 'r-lea', againstBookingId: null }
    let calls = 0
    const backend = install({
      extra: [{
        method: 'PATCH',
        match: new RegExp(`${PLANNING}/bookings/b-1$`),
        handler: () => {
          calls += 1
          return calls === 1
            ? status(422, { error: { code: 'unprocessable_entity', message: 'warns', details: { reason: 'planning_warnings_unconfirmed', codes: ['outside_working_hours'], unconfirmed: [shown], warnings: [shown] } } })
            : { booking: WORK, markedForReplanning: [] }
        },
      }],
    })
    render()

    await user.click((await screen.findAllByRole('button', { name: /Auftrag 1001/ }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.booking.save') }))

    const warning = await within(dialog).findByRole('alert')
    expect(warning).toHaveTextContent(i18n.t('planner.warnings.outside_working_hours', { resource: 'Lea Meier' }))
    expect(warning).toHaveTextContent(i18n.t('planner.booking.warningsNote'))
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.booking.saveAnyway') }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const sent = backend.callsTo(/\/bookings\/b-1$/, 'PATCH')
    expect(sent).toHaveLength(2)
    expect((sent[1].body as { confirmedWarnings: unknown[] }).confirmedWarnings).toEqual([shown])
  })

  it('an overruled warning on a booking is shown with the name and the time, and the cause of «neu einplanen»', async () => {
    const user = userEvent.setup()
    install()
    render()

    await user.click(await itemNamed('Auftrag 1002'))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('planner.booking.overruled', {
      warning: i18n.t('planner.warnings.outside_working_hours', { resource: 'Lea Meier' }),
      name: 'Anna Chef',
      at: '27.02.2027 10:05',
    }))).toBeInTheDocument()
    expect(within(dialog).getByText(new RegExp(i18n.t('planner.booking.causes.blocked')))).toBeInTheDocument()
  })

  it('a booking is released after a confirmation, with If-Match', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [{ method: 'POST', match: new RegExp(`${PLANNING}/bookings/b-1/release$`), handler: () => ({ ...WORK, status: 'released', version: 3 }) }],
    })
    render()

    await user.click((await screen.findAllByRole('button', { name: /Auftrag 1001/ }))[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.booking.release') }))
    expect(backend.callsTo(/\/release$/, 'POST')).toHaveLength(0)
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.booking.releaseConfirmButton') }))

    await waitFor(() => expect(backend.callsTo(/\/release$/, 'POST')).toHaveLength(1))
    expect(backend.callsTo(/\/release$/, 'POST')[0].headers.get('If-Match')).toBe('2')
  })

  it('a block is made with an optional note and says how many bookings it marked', async () => {
    const user = userEvent.setup()
    const backend = install({
      extra: [{ method: 'POST', match: new RegExp(`${PLANNING}/blocks$`), handler: () => ({ booking: BLOCK, markedForReplanning: [MARKED] }) }],
    })
    render()

    await user.click(await screen.findByRole('button', { name: i18n.t('planner.blockResource') }))
    const dialog = await screen.findByRole('dialog', { name: i18n.t('planner.block.title') })
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.block.submit') }))
    expect(within(dialog).getByText(i18n.t('planner.booking.needsResource'))).toBeInTheDocument()
    expect(backend.callsTo(/\/blocks$/, 'POST')).toHaveLength(0)

    await user.click(within(dialog).getByRole('textbox', { name: i18n.t('planner.fields.workplaces') }))
    await user.click(await screen.findByRole('option', { name: 'Bühne 1' }))
    await user.click(within(dialog).getByRole('button', { name: i18n.t('planner.block.submit') }))

    await waitFor(() => expect(backend.callsTo(/\/blocks$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/blocks$/, 'POST')
    expect(call.body).toEqual({ resourceIds: ['r-lift'], startsAt: '2027-03-01T07:00:00+01:00', endsAt: '2027-03-01T18:00:00+01:00', note: null })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
    expect(await screen.findByText(i18n.t('planner.block.marked', { count: 1 }))).toBeInTheDocument()
  })

  it('a location without a workplace says so and where to set it up', async () => {
    install({ resources: [LEA], bookings: [] })
    render()

    expect(await screen.findByText(i18n.t('planner.setup.noWorkplace'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('planner.setup.where'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('planner.setup.open') })).toHaveAttribute('href', '/administration/planner-resources')
  })

  it('a location with nobody and nothing says so, and the grid not set says where to set it', async () => {
    install({ resources: [], bookings: [], grid: null })
    render()

    expect(await screen.findByText(i18n.t('planner.setup.nothing'))).toBeInTheDocument()
    expect(await screen.findByText(i18n.t('planner.gridNotSet'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('planner.openSettings') })).toHaveAttribute('href', '/administration/settings/planning')
    expect(screen.queryByRole('group', { name: /Hauptsitz/ })).not.toBeInTheDocument()
  })

  it.each(['fr', 'it', 'en'])('speaks %s: the day, the rows, the types and a refusal', async (language) => {
    await i18n.changeLanguage(language)
    try {
      install()
      render()
      const grid = await screen.findByRole('group', { name: new RegExp(i18n.t('planner.grid.aria', { day: '.*', location: 'Hauptsitz' })) })
      expect(within(grid).getByText(i18n.t('planner.groups.people'))).toBeInTheDocument()
      expect(within(grid).getByText(i18n.t('planner.groups.workplaces'))).toBeInTheDocument()
      if (language === 'fr') {
        expect(within(grid).getByText('Mécatronicien')).toBeInTheDocument()
        expect(within(grid).getByText('Pont élévateur')).toBeInTheDocument()
      }
      expect(screen.getByRole('button', { name: i18n.t('planner.blockResource') })).toBeInTheDocument()
      expect(i18n.t('apiRefusal.reasons.planning_workplace_taken')).not.toBe('apiRefusal.reasons.planning_workplace_taken')
    } finally {
      await i18n.changeLanguage('de')
    }
  })
})

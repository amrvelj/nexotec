// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status, type FakeRequest, type FakeRoute } from '../test/fakeBackend'
import { NumberRangesPage } from './NumberRangesPage'

// KAN-207 — Dealer Administration › Number ranges: one row per document type
// with its pattern, the next number and the current value read-only; the
// freeze said before the first number (A-RULE-3); an open range edited by
// the manager with a live example, a frozen one only by platform staff,
// with a reason.

const range = (overrides: Record<string, unknown> = {}) => ({
  documentType: 'offer',
  module: 'sales',
  pattern: 'O-{counter:6}',
  startValue: 1,
  yearlyReset: false,
  lastValue: 123,
  nextNumber: 'O-000124',
  frozenAt: null,
  version: 2,
  ...overrides,
})

const INVOICE = range({
  documentType: 'invoice', module: 'finance', pattern: 'R-{year}-{counter:6}', yearlyReset: true, lastValue: 41,
  nextNumber: 'R-2026-000042', frozenAt: '2026-10-01T08:00:00Z', version: 9,
})

function install(extra: FakeRoute[] = [], mayChangeFrozen = false) {
  return installFakeBackend([
    ...extra,
    {
      method: 'GET',
      match: /^\/dealerships\/dealership-1\/number-ranges$/,
      handler: () => ({ items: [range(), INVOICE], mayChangeFrozen }),
    },
  ])
}

afterEach(() => {
  cleanup()
})

describe('NumberRangesPage (KAN-207)', () => {
  it('lists each range with its next number and current value, and says the freeze before the first number', async () => {
    install()
    renderWithProviders(<NumberRangesPage />, { route: '/administration/number-ranges' })

    const table = await screen.findByRole('table', { name: i18n.t('numberRanges.title') })
    const offer = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('numberRanges.documentType.offer')) })
    expect(within(offer).getByText('O-{counter:6}')).toBeInTheDocument()
    expect(within(offer).getByText('O-000124')).toBeInTheDocument()
    expect(within(offer).getByText('123')).toBeInTheDocument()
    expect(within(offer).getByText(i18n.t('numberRanges.status.open'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('numberRanges.freezeNotice'))).toBeInTheDocument()

    const invoice = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('numberRanges.documentType.invoice')) })
    expect(within(invoice).getByText(i18n.t('numberRanges.status.frozen'))).toBeInTheDocument()
    // A frozen range offers the manager no edit (A-RULE-3).
    expect(within(invoice).queryByRole('button', { name: i18n.t('numberRanges.edit') })).not.toBeInTheDocument()
    expect(within(offer).getByRole('button', { name: i18n.t('numberRanges.edit') })).toBeInTheDocument()
  })

  it('the manager edits an open range with a live example and saves under If-Match', async () => {
    const user = userEvent.setup()
    const backend = install([
      {
        method: 'POST',
        match: /\/number-range-previews$/,
        handler: (req: FakeRequest) => {
          const body = req.body as { pattern: string }
          return body.pattern.includes('{location_code}')
            ? status(422, {
                error: { code: 'unprocessable', message: 'This document type does not know its location yet.', details: { reason: 'number_pattern_location_unavailable' } },
              })
            : { nextNumber: body.pattern === 'OF-{counter:5}' ? 'OF-00124' : 'O-000124' }
        },
      },
      {
        method: 'PATCH',
        match: /\/number-ranges\/offer$/,
        handler: (req: FakeRequest) => range({ ...(req.body as object), nextNumber: 'OF-00124', version: 3 }),
      },
    ])
    renderWithProviders(<NumberRangesPage />, { route: '/administration/number-ranges' })

    const table = await screen.findByRole('table', { name: i18n.t('numberRanges.title') })
    const offer = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('numberRanges.documentType.offer')) })
    await user.click(within(offer).getByRole('button', { name: i18n.t('numberRanges.edit') }))
    const dialog = await screen.findByRole('dialog')
    const pattern = within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('numberRanges.pattern')) })

    await user.clear(pattern)
    await user.type(pattern, 'O-{{location_code}{{counter:5}')
    expect(await within(dialog).findByText(i18n.t('apiRefusal.reasons.number_pattern_location_unavailable'))).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: i18n.t('common.save') })).toBeDisabled()

    await user.clear(pattern)
    await user.type(pattern, 'OF-{{counter:5}')
    expect(await within(dialog).findByText('OF-00124')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: i18n.t('common.save') }))

    await waitFor(() => expect(backend.callsTo(/\/number-ranges\/offer$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/number-ranges\/offer$/, 'PATCH')
    expect(patch!.body).toEqual({ pattern: 'OF-{counter:5}', startValue: 1, yearlyReset: false })
    expect(patch!.headers.get('If-Match')).toBe('2')
    expect(await within(table).findByText('OF-{counter:5}')).toBeInTheDocument()
  })

  it('platform staff change a frozen range only with a reason', async () => {
    const user = userEvent.setup()
    const backend = install(
      [
        { method: 'POST', match: /\/number-range-previews$/, handler: () => ({ nextNumber: 'R-2026-000042' }) },
        { method: 'PATCH', match: /\/number-ranges\/invoice$/, handler: () => ({ ...INVOICE, version: 10 }) },
      ],
      true,
    )
    renderWithProviders(<NumberRangesPage />, { route: '/administration/number-ranges' })

    const table = await screen.findByRole('table', { name: i18n.t('numberRanges.title') })
    const invoice = within(table).getByRole('row', { name: (n) => n.startsWith(i18n.t('numberRanges.documentType.invoice')) })
    await user.click(within(invoice).getByRole('button', { name: i18n.t('numberRanges.edit') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(i18n.t('numberRanges.frozenStaffNotice'))).toBeInTheDocument()
    const save = within(dialog).getByRole('button', { name: i18n.t('common.save') })
    await waitFor(() => expect(within(dialog).getByText('R-2026-000042')).toBeInTheDocument())
    expect(save).toBeDisabled() // no reason yet

    await user.type(within(dialog).getByRole('textbox', { name: (n) => n.startsWith(i18n.t('numberRanges.reason')) }), 'Treuhänder-Vorgabe')
    await user.click(save)

    await waitFor(() => expect(backend.callsTo(/\/number-ranges\/invoice$/, 'PATCH')).toHaveLength(1))
    expect(backend.callsTo(/\/number-ranges\/invoice$/, 'PATCH')[0]!.body).toMatchObject({ reason: 'Treuhänder-Vorgabe' })
  })
})

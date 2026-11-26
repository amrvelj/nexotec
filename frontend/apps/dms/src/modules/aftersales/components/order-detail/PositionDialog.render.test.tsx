// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { installFakeBackend, status, type FakeRoute } from '../../../../test/fakeBackend'
import type { LabourRateProposalRead } from '../../../../api/types'
import { PositionDialog } from './PositionDialog'
import { JOB_ID, LABOUR_BY_TIME, LABOUR_CATEGORIES, ORDER_ID, order, proposal } from '../../test/serviceOrderFixtures'

// KAN-810 criterion 3 (D13): labour by time proposes the rate «which applies here» and names its
// source (check 11); the price typed last leads and is sent with `priceEntered` (check 12); the time
// is typed in the unit in force (work units here, ruling 183) and a position keeps its unit (check 48);
// fixed-price labour is always typed (Anto, Q2).

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options)
const POSITIONS = new RegExp(`^/service-orders/${ORDER_ID}/jobs/${JOB_ID}/positions$`)

function install(options: { proposal?: Partial<LabourRateProposalRead>; post?: FakeRoute['handler'] } = {}) {
  return installFakeBackend([
    { method: 'GET', match: /^\/dealerships\/dealership-1\/labour-rate-categories$/, handler: () => LABOUR_CATEGORIES },
    { method: 'GET', match: /^\/dealerships\/dealership-1$/, handler: () => ({ id: 'dealership-1', vatRate: '8.10' }) },
    { method: 'GET', match: /\/labour-rate-proposal$/, handler: () => proposal(options.proposal) },
    { method: 'POST', match: POSITIONS, handler: options.post ?? (() => order()) },
    { method: 'PATCH', match: /\/positions\/pos-1$/, handler: () => order() },
  ])
}

const field = (label: string) => screen.getByRole('textbox', { name: (name) => name.startsWith(label) })

function renderDialog(position = null as typeof LABOUR_BY_TIME | null) {
  const onSaved = vi.fn()
  renderWithProviders(
    <PositionDialog opened order={{ id: ORDER_ID, version: 3 }} jobId={JOB_ID} position={position} onClose={() => {}} onSaved={onSaved} onReload={() => {}} />,
  )
  return { onSaved }
}

async function chooseCategory(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('textbox', { name: (name) => name.startsWith(t('serviceOrder.positionDialog.labourCategory')) }))
  await user.click(await screen.findByRole('option', { name: 'Mechanik' }))
}

describe('the position dialog (KAN-810, D13)', () => {
  it('labour by time: the proposal names its source; left as proposed, no price is sent; the time goes in work units', async () => {
    const user = userEvent.setup()
    const backend = install()
    const { onSaved } = renderDialog()

    await user.type(field(t('serviceOrder.positionDialog.text')), 'Jahresservice')
    await chooseCategory(user)

    expect(await screen.findByTestId('rate-proposal')).toHaveTextContent(
      t('serviceOrder.positionDialog.proposalFrom', { source: t('serviceOrder.rateSource.agreement'), date: '01.11.2026' }),
    )
    // The Betrieb works in work units here: the field and the line say so.
    expect(field(t('serviceOrder.positionDialog.quantityIn', { unit: 'AW' }))).toBeInTheDocument()
    expect(screen.getByText(t('serviceOrder.positionDialog.workUnitsPerHour', { count: 12 }))).toBeInTheDocument()
    expect(field(t('serviceOrder.positionDialog.rateNet'))).toHaveValue("CHF 148.00")
    expect(field(t('serviceOrder.positionDialog.rateGross'))).toHaveValue("CHF 159.99")

    await user.type(field(t('serviceOrder.positionDialog.quantityIn', { unit: 'AW' })), '9')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    const [post] = backend.callsTo(POSITIONS, 'POST')
    expect(post.body).toEqual({ kind: 'labour_time', text: 'Jahresservice', labourCategoryId: 'cat-1', time: '9', timeUnit: 'work_units' })
    expect(post.headers.get('If-Match')).toBe('3')
    expect(post.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('a typed gross price leads: it is sent as the price, entered gross; the net shown follows', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderDialog()

    await user.type(field(t('serviceOrder.positionDialog.text')), 'Diagnose')
    await chooseCategory(user)
    await screen.findByTestId('rate-proposal')
    await user.type(field(t('serviceOrder.positionDialog.quantityIn', { unit: 'AW' })), '6')
    const gross = field(t('serviceOrder.positionDialog.rateGross'))
    await user.clear(gross)
    await user.type(gross, '170')
    await user.tab()
    expect(field(t('serviceOrder.positionDialog.rateNet'))).toHaveValue('CHF 157.26')

    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    await waitFor(() => expect(backend.callsTo(POSITIONS, 'POST')).toHaveLength(1))
    expect(backend.callsTo(POSITIONS, 'POST')[0].body).toEqual({
      kind: 'labour_time',
      text: 'Diagnose',
      labourCategoryId: 'cat-1',
      time: '6',
      timeUnit: 'work_units',
      unitPrice: '170',
      priceEntered: 'gross',
    })
  })

  it('fixed-price labour is always typed: no proposal, and «Speichern» waits for the price', async () => {
    const user = userEvent.setup()
    const backend = install({ proposal: { timeUnit: 'hours', workUnitsPerHour: null } })
    renderDialog()

    await user.type(field(t('serviceOrder.positionDialog.text')), 'Pauschale Bremsen')
    await chooseCategory(user)
    await user.click(screen.getByRole('checkbox', { name: t('serviceOrder.positionDialog.byTime') }))
    expect(screen.queryByTestId('rate-proposal')).not.toBeInTheDocument()
    expect(field(t('serviceOrder.positionDialog.fixedNet'))).toHaveValue('')
    expect(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') })).toBeDisabled()

    await user.type(field(t('serviceOrder.positionDialog.fixedNet')), '250')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    await waitFor(() => expect(backend.callsTo(POSITIONS, 'POST')).toHaveLength(1))
    expect(backend.callsTo(POSITIONS, 'POST')[0].body).toEqual({
      kind: 'labour_fixed',
      text: 'Pauschale Bremsen',
      labourCategoryId: 'cat-1',
      unitPrice: '250',
      priceEntered: 'net',
    })
  })

  it('a text position carries its description only', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderDialog()

    await user.click(screen.getByRole('radio', { name: t('serviceOrder.positionDialog.kinds.text') }))
    expect(screen.queryByRole('textbox', { name: (name) => name.startsWith(t('serviceOrder.positionDialog.labourCategory')) })).not.toBeInTheDocument()
    await user.type(field(t('serviceOrder.positionDialog.text')), 'Ersatzwagen bis 17 Uhr')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    await waitFor(() => expect(backend.callsTo(POSITIONS, 'POST')).toHaveLength(1))
    expect(backend.callsTo(POSITIONS, 'POST')[0].body).toEqual({ kind: 'text', text: 'Ersatzwagen bis 17 Uhr' })
  })

  it('without a rate here it says so, and a refusal is said in one sentence', async () => {
    const user = userEvent.setup()
    install({
      proposal: { found: false, source: null, unitPrice: null, validFrom: null },
      post: () =>
        status(409, { error: { code: 'conflict', message: 'No labour rate applies', details: { reason: 'service_order_labour_rate_missing' } } }),
    })
    renderDialog()

    await user.type(field(t('serviceOrder.positionDialog.text')), 'Diagnose')
    await chooseCategory(user)
    expect(await screen.findByTestId('rate-proposal')).toHaveTextContent(t('serviceOrder.positionDialog.noProposal'))
    await user.type(field(t('serviceOrder.positionDialog.quantityIn', { unit: 'AW' })), '3')
    await user.type(field(t('serviceOrder.positionDialog.rateNet')), '120')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    expect(await screen.findByRole('alert')).toHaveTextContent(t('apiRefusal.reasons.service_order_labour_rate_missing'))
  })

  it('a position keeps the unit it was entered with; a changed time is sent in that unit', async () => {
    const user = userEvent.setup()
    // The Betrieb has gone over to hours since; the position was typed in work units.
    const backend = install({ proposal: { timeUnit: 'hours', workUnitsPerHour: null } })
    renderDialog(LABOUR_BY_TIME)

    const time = await screen.findByRole('textbox', { name: (name) => name.startsWith(t('serviceOrder.positionDialog.quantityIn', { unit: 'AW' })) })
    expect(time).toHaveValue('9')
    expect(screen.getByText(t('serviceOrder.positionDialog.keepsUnit', { unit: t('serviceOrder.unitName.work_units') }))).toBeInTheDocument()
    await user.clear(time)
    await user.type(time, '12')
    await user.click(screen.getByRole('button', { name: t('serviceOrder.positionDialog.save') }))
    await waitFor(() => expect(backend.callsTo(/\/positions\/pos-1$/, 'PATCH')).toHaveLength(1))
    const [patch] = backend.callsTo(/\/positions\/pos-1$/, 'PATCH')
    expect(patch.body).toEqual({ time: '12', timeUnit: 'work_units' })
    expect(patch.headers.get('If-Match')).toBe('3')
  })
})

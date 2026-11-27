// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { Route, Routes } from 'react-router-dom'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../../../../i18n'
import { renderWithProviders } from '../../../../test/renderWithProviders'
import { ALL_ENTITLEMENTS, ALL_GATED_CAPABILITIES, installFakeBackend, status, type FakeRoute } from '../../../../test/fakeBackend'
import type { ServiceOrderJobRead, ServiceOrderRead } from '../../../../api/types'
import { ServiceOrderDetailPage } from '../../pages/ServiceOrderDetailPage'
import { HISTORY, JOB_ID, LABOUR_BY_TIME, ORDER_ID, order } from '../../test/serviceOrderFixtures'

// KAN-811 (AS1-08), D8 «Antwort erfassen»: how the answer came, per job its amount and the
// answer, a note required on a decline, «hours billed?» for an accepted job with clocked time;
// the job card says who answered how and when, who entered each price, «vorher angenommen» and
// «gestoppt»; the order shows «Arbeit ohne Antwort».

const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options)
const ORDER_PATH = new RegExp(`^/service-orders/${ORDER_ID}$`)

const me: FakeRoute = {
  method: 'GET',
  match: /\/auth\/me$/,
  handler: () => ({
    user: {
      id: 'user-1', dealershipId: 'dealership-1', firstName: 'Sandra', lastName: 'Keller', email: 'sk@example.ch', phone: null,
      role: 'service_advisor', accessRoles: ['aftersales'], isDealerManager: false, employmentStatus: 'active',
      authIdentityId: 'auth-1', status: 'active', version: 1, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
    },
    activeDealership: { id: 'dealership-1', legalName: 'Garage Muster AG', status: 'active' },
    memberships: [{ id: 'dealership-1', legalName: 'Garage Muster AG' }],
    entitlements: ALL_ENTITLEMENTS,
    writableCapabilities: ALL_GATED_CAPABILITIES,
  }),
}

function waitingJob(overrides: Partial<ServiceOrderJobRead> = {}): ServiceOrderJobRead {
  return {
    ...order().jobs[0],
    id: 'job-2',
    name: 'Bremsen vorne',
    answerState: 'waiting',
    answeredAt: null,
    answeredBy: null,
    positions: [],
    amount: { net: '551.20', gross: '595.85' },
    answers: [],
    ...overrides,
  }
}

function install(current: ServiceOrderRead, extra: FakeRoute[] = []) {
  return installFakeBackend([
    ...extra,
    me,
    { method: 'GET', match: ORDER_PATH, handler: () => current },
    { method: 'GET', match: /\/history$/, handler: () => HISTORY },
  ])
}

function renderPage(search = '') {
  renderWithProviders(
    <Routes>
      <Route path="/aftersales/orders/:id" element={<ServiceOrderDetailPage />} />
    </Routes>,
    { route: `/aftersales/orders/${ORDER_ID}${search}` },
  )
}

describe('D8 — the customer\'s answer (KAN-811)', () => {
  it('«Antwort erfassen» is the primary; the answer is sent with how it came, per job, under If-Match', async () => {
    const current = order({ jobs: [order().jobs[0], waitingJob()] })
    const backend = install(current, [
      { method: 'POST', match: /\/answers$/, handler: () => order() },
    ])
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: t('serviceOrder.actions.recordAnswer') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByTestId('answer-contact')).toHaveTextContent('Reto Wyss')
    const brakes = within(dialog).getByTestId('answer-job-job-2')
    expect(within(brakes).getByText("CHF 551.20 · CHF 595.85 inkl. MWST")).toBeInTheDocument()

    await user.click(within(dialog).getByRole('textbox', { name: t('serviceOrder.answer.channelLabel') }))
    await user.click(await screen.findByRole('option', { name: t('serviceOrder.answer.channel.phone') }))
    await user.click(within(brakes).getByRole('textbox', { name: t('serviceOrder.answer.resultFor', { name: 'Bremsen vorne' }) }))
    await user.click(await screen.findByRole('option', { name: t('serviceOrder.answer.result.accepted') }))
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.answer.save') }))

    await waitFor(() => expect(backend.calls.some((c) => c.method === 'POST')).toBe(true))
    const call = backend.calls.find((c) => c.method === 'POST')!
    expect(call.body).toEqual({ channel: 'phone', jobs: [{ jobId: 'job-2', result: 'accepted' }] })
    expect(call.headers.get('If-Match')).toBe('3')
  })

  it('a decline needs its note before anything is sent', async () => {
    const backend = install(order({ jobs: [waitingJob()] }))
    const user = userEvent.setup()
    renderPage('?dialog=answer')

    const dialog = await screen.findByRole('dialog')
    const brakes = within(dialog).getByTestId('answer-job-job-2')
    await user.click(within(brakes).getByRole('textbox', { name: t('serviceOrder.answer.resultFor', { name: 'Bremsen vorne' }) }))
    await user.click(await screen.findByRole('option', { name: t('serviceOrder.answer.result.declined') }))
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.answer.save') }))

    expect(await within(dialog).findByText(t('serviceOrder.answer.noteMissing'))).toBeInTheDocument()
    expect(backend.calls.some((c) => c.method === 'POST')).toBe(false)
  })

  it('declining an accepted job with clocked time asks whether the hours are billed (check 8)', async () => {
    const accepted = { ...order().jobs[0], timeClocked: true }
    const backend = install(order({ jobs: [accepted] }), [
      { method: 'POST', match: /\/answers$/, handler: () => order() },
    ])
    const user = userEvent.setup()
    renderPage('?dialog=answer')

    const dialog = await screen.findByRole('dialog')
    const job = within(dialog).getByTestId(`answer-job-${JOB_ID}`)
    await user.click(within(job).getByRole('textbox', { name: t('serviceOrder.answer.resultFor', { name: 'Jahresservice Flotte' }) }))
    await user.click(await screen.findByRole('option', { name: t('serviceOrder.answer.result.declined') }))
    await user.type(within(job).getByRole('textbox', { name: t('serviceOrder.answer.noteFor', { name: 'Jahresservice Flotte' }) }), 'Kunde bricht ab')
    expect(within(job).getByText(t('serviceOrder.answer.hoursQuestion'))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.answer.save') }))
    expect(await within(dialog).findByText(t('serviceOrder.answer.hoursMissing'))).toBeInTheDocument()

    await user.click(within(job).getByRole('radio', { name: t('serviceOrder.answer.hoursNotBilled') }))
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.answer.save') }))
    await waitFor(() => expect(backend.calls.some((c) => c.method === 'POST')).toBe(true))
    expect(backend.calls.find((c) => c.method === 'POST')!.body).toEqual({
      channel: 'desk',
      jobs: [{ jobId: JOB_ID, result: 'declined', note: 'Kunde bricht ab', hoursBilled: false }],
    })
  })

  it('a refusal is said in one sentence in the UI language', async () => {
    install(order({ jobs: [waitingJob()] }), [
      {
        method: 'POST',
        match: /\/answers$/,
        handler: () => status(409, { error: { code: 'conflict', message: 'x', details: { reason: 'service_order_job_stopped' } } }),
      },
    ])
    const user = userEvent.setup()
    renderPage('?dialog=answer')
    const dialog = await screen.findByRole('dialog')
    const brakes = within(dialog).getByTestId('answer-job-job-2')
    await user.click(within(brakes).getByRole('textbox', { name: t('serviceOrder.answer.resultFor', { name: 'Bremsen vorne' }) }))
    await user.click(await screen.findByRole('option', { name: t('serviceOrder.answer.result.accepted') }))
    await user.click(within(dialog).getByRole('button', { name: t('serviceOrder.answer.save') }))
    expect(await within(dialog).findByText(t('apiRefusal.reasons.service_order_job_stopped'))).toBeInTheDocument()
  })

  it('the job card says who answered how and when, who entered the price, «vorher angenommen» and «gestoppt»', async () => {
    const waitingAgain = waitingJob({
      acceptedBefore: { net: '434.85', gross: '470.07' },
      answers: [{ ...order().jobs[0].answers![0], id: 'a-2' }],
      positions: [{ ...LABOUR_BY_TIME, id: 'pos-9', jobId: 'job-2' }],
    })
    const stopped = waitingJob({ id: 'job-3', name: 'Klima', answerState: 'declined', stopped: true, hoursBilled: false })
    install(order({ jobs: [order().jobs[0], waitingAgain, stopped] }))
    renderPage()

    const first = await screen.findByTestId(`job-${JOB_ID}`)
    expect(within(first).getByTestId(`last-answer-${JOB_ID}`)).toHaveTextContent(
      `${t('serviceOrder.answer.result.accepted')} ${t('serviceOrder.answer.channel.phone')} · Sandra Keller`,
    )
    expect(within(first).getByTestId('priced-by-pos-1')).toHaveTextContent(/^Preis erfasst von Sandra Keller, /)
    const again = screen.getByTestId('job-job-2')
    expect(within(again).getByText(t('serviceOrder.answer.acceptedBefore', { amount: 'CHF 470.07' }))).toBeInTheDocument()
    const klima = screen.getByTestId('job-job-3')
    expect(within(klima).getByText(t('serviceOrder.answer.stoppedNotBilled'))).toBeInTheDocument()
    // Decision 54: a stopped job takes no new position.
    expect(within(klima).getByRole('button', { name: t('serviceOrder.jobs.addPosition') })).toBeDisabled()
  })

  it('«Arbeit ohne Antwort» shows on the order while a job waits and somebody works on it', async () => {
    install(order({ workWithoutAnswer: true, jobs: [waitingJob({ workWithoutAnswer: true })] }))
    renderPage()
    expect(await screen.findByTestId('work-without-answer')).toHaveTextContent('Bremsen vorne')
  })
})

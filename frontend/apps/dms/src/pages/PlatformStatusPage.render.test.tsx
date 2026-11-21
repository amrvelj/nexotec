// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { PlatformStatusPage } from './PlatformStatusPage'

// KAN-223 — Platform Administration › Nexotec status (D-A-38): staff record
// an incident in all four languages and end a current one under If-Match.

const CURRENT = {
  id: 'inc-1',
  severity: 'blocked',
  title: { de: 'Ausfall der Inserate', fr: 'Panne des annonces', it: 'Guasto degli annunci', en: 'Listings outage' },
  body: { de: 'Text', fr: 'Texte', it: 'Testo', en: 'Text' },
  startsAt: '2026-11-15T08:00:00Z',
  endsAt: null,
  version: 3,
}

afterEach(() => {
  cleanup()
})

describe('PlatformStatusPage (KAN-223)', () => {
  const t = (key: string, options?: Record<string, unknown>) => i18n.getFixedT('de')(key, options)

  it('ends a current incident under If-Match', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/platform\/status-incidents$/, handler: () => ({ items: [CURRENT] }) },
      { method: 'PATCH', match: /^\/platform\/status-incidents\/inc-1$/, handler: () => ({ ...CURRENT, version: 4 }) },
    ])
    renderWithProviders(<PlatformStatusPage />, { route: '/platform/status' })
    const table = await screen.findByRole('table', { name: t('statusPage.platform.title') })
    const row = within(table).getByRole('row', { name: (n) => n.includes('Ausfall der Inserate') })
    expect(within(row).getByText(t('statusPage.platform.current'))).toBeInTheDocument()
    await userEvent.click(within(row).getByRole('button', { name: t('statusPage.platform.end') }))
    await waitFor(() => expect(backend.callsTo(/\/platform\/status-incidents\/inc-1$/, 'PATCH')).toHaveLength(1))
    const [call] = backend.callsTo(/\/platform\/status-incidents\/inc-1$/, 'PATCH')
    expect(call.headers.get('If-Match')).toBe('3')
    expect(call.body).toEqual({ endsAt: expect.any(String) })
  })

  it('records an incident only once every language has a title and a text', async () => {
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/platform\/status-incidents$/, handler: () => ({ items: [] }) },
      { method: 'POST', match: /^\/platform\/status-incidents$/, handler: () => ({ ...CURRENT, id: 'inc-2' }) },
    ])
    renderWithProviders(<PlatformStatusPage />, { route: '/platform/status' })
    expect(await screen.findByText(t('statusPage.platform.empty'))).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: t('statusPage.platform.new') }))
    const dialog = await screen.findByRole('dialog')
    const save = within(dialog).getByRole('button', { name: t('statusPage.platform.form.save') })
    expect(save).toBeDisabled()

    const fields: Array<[string, string]> = [
      ['Deutsch', 'Störung'],
      ['Français', 'Panne'],
      ['Italiano', 'Guasto'],
      ['English', 'Outage'],
    ]
    for (const [endonym, text] of fields) {
      await userEvent.type(within(dialog).getByLabelText(t('statusPage.platform.form.titleIn', { language: endonym })), text)
      await userEvent.type(within(dialog).getByLabelText(t('statusPage.platform.form.bodyIn', { language: endonym })), text)
    }
    expect(save).toBeEnabled()
    await userEvent.click(save)
    await waitFor(() => expect(backend.callsTo(/\/platform\/status-incidents$/, 'POST')).toHaveLength(1))
    const [call] = backend.callsTo(/\/platform\/status-incidents$/, 'POST')
    expect(call.body).toMatchObject({
      severity: 'blocked',
      title: { de: 'Störung', fr: 'Panne', it: 'Guasto', en: 'Outage' },
      endsAt: null,
    })
  })

  // KAN-776 — Nexotec's Business Profile client: staff see the cause.
  it('shows the Business Profile client interrupted with Google\'s refusal and the bound businesses', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/platform\/status-incidents$/, handler: () => ({ items: [] }) },
      {
        method: 'GET',
        match: /^\/integrations\/business-profile\/client-status$/,
        handler: () => ({
          configured: true,
          interrupted: true,
          breakerOpen: true,
          refusal: 'SERVICE_DISABLED',
          refusedAt: '2026-11-20T09:00:00Z',
          lastCallAt: '2026-11-20T09:00:00Z',
          boundDealerships: 3,
        }),
      },
    ])
    renderWithProviders(<PlatformStatusPage />, { route: '/platform/status' })
    const panel = await screen.findByRole('region', { name: t('statusPage.platform.businessProfile.title') })
    expect(await within(panel).findByText(t('statusPage.platform.businessProfile.state.interrupted'))).toBeInTheDocument()
    expect(within(panel).getByText(t('statusPage.platform.businessProfile.cause.breakerOpen'))).toBeInTheDocument()
    expect(within(panel).getByText((text) => text.includes('SERVICE_DISABLED'))).toBeInTheDocument()
    expect(within(panel).getByText((text) => text.includes(t('statusPage.platform.businessProfile.bound', { count: 3 })))).toBeInTheDocument()
  })

  it('shows the Business Profile client working when nothing refused it', async () => {
    installFakeBackend([
      { method: 'GET', match: /^\/platform\/status-incidents$/, handler: () => ({ items: [] }) },
      {
        method: 'GET',
        match: /^\/integrations\/business-profile\/client-status$/,
        handler: () => ({
          configured: true,
          interrupted: false,
          breakerOpen: false,
          refusal: null,
          refusedAt: null,
          lastCallAt: null,
          boundDealerships: 0,
        }),
      },
    ])
    renderWithProviders(<PlatformStatusPage />, { route: '/platform/status' })
    const panel = await screen.findByRole('region', { name: t('statusPage.platform.businessProfile.title') })
    expect(await within(panel).findByText(t('statusPage.platform.businessProfile.state.working'))).toBeInTheDocument()
    expect(within(panel).getByText((text) => text.includes(t('statusPage.platform.businessProfile.noCall')))).toBeInTheDocument()
    expect(within(panel).queryByText((text) => text.includes('SERVICE_DISABLED'))).toBeNull()
  })

  // KAN-776, Anto's decision 1 A: «Erneut prüfen» asks Google once; a success turns the panel green.
  it('checks the client again and shows Google\'s answer', async () => {
    const interrupted = {
      configured: true,
      interrupted: true,
      breakerOpen: false,
      refusal: 'SERVICE_DISABLED',
      refusedAt: '2026-11-20T09:00:00Z',
      lastCallAt: '2026-11-20T09:00:00Z',
      boundDealerships: 2,
    }
    const backend = installFakeBackend([
      { method: 'GET', match: /^\/platform\/status-incidents$/, handler: () => ({ items: [] }) },
      { method: 'GET', match: /^\/integrations\/business-profile\/client-status$/, handler: () => interrupted },
      {
        method: 'POST',
        match: /^\/integrations\/business-profile\/client-status\/check$/,
        handler: () => ({
          outcome: 'answered',
          status: { ...interrupted, interrupted: false, refusal: null, refusedAt: null, lastCallAt: '2026-11-21T08:00:00Z' },
        }),
      },
    ])
    renderWithProviders(<PlatformStatusPage />, { route: '/platform/status' })
    const panel = await screen.findByRole('region', { name: t('statusPage.platform.businessProfile.title') })
    expect(await within(panel).findByText(t('statusPage.platform.businessProfile.state.interrupted'))).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: t('statusPage.platform.businessProfile.check') }))
    await waitFor(() => expect(backend.callsTo(/\/client-status\/check$/, 'POST')).toHaveLength(1))
    expect(await within(panel).findByText(t('statusPage.platform.businessProfile.outcome.answered'))).toBeInTheDocument()
    expect(within(panel).getByText(t('statusPage.platform.businessProfile.state.working'))).toBeInTheDocument()
    expect(within(panel).queryByText((text) => text.includes('SERVICE_DISABLED'))).toBeNull()
  })
})

// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { StatusPage } from './StatusPage'

// KAN-223 — Dealer Administration › Status («Alles grün», D-A-38): every
// item its source derives, grouped by source; green only when every item is;
// a degraded item names itself and links to the screen that fixes it;
// Nexotec's incident shows its own text in the UI language.

const INCIDENT = {
  id: 'inc-1',
  severity: 'warning',
  title: { de: 'Langsame Inserate', fr: 'Annonces lentes', it: 'Annunci lenti', en: 'Slow listings' },
  body: { de: 'Wir arbeiten daran.', fr: 'Nous y travaillons.', it: 'Ci stiamo lavorando.', en: 'We are on it.' },
  startsAt: '2026-11-15T08:00:00Z',
  endsAt: null,
  version: 1,
}

const item = (overrides: Record<string, unknown>) => ({
  key: 'number_range.offer',
  source: 'number_ranges',
  state: 'ok',
  messageKey: 'statusPage.items.numberRange.ok',
  params: { documentType: 'offer', percent: 12 },
  link: '/administration/number-ranges',
  incident: null,
  ...overrides,
})

const GREEN = {
  dealershipId: 'dealership-1',
  state: 'ok',
  attentionCount: 0,
  items: [item({}), item({ key: 'nexotec.platform', source: 'nexotec', messageKey: 'statusPage.items.nexotec.ok', params: {}, link: null })],
}

const DEGRADED = {
  dealershipId: 'dealership-1',
  state: 'blocked',
  attentionCount: 2,
  items: [
    item({ state: 'warning', messageKey: 'statusPage.items.numberRange.nearlyExhausted', params: { documentType: 'offer', percent: 93 } }),
    item({
      key: 'integration.connection.c-1',
      source: 'integration',
      state: 'blocked',
      messageKey: 'statusPage.items.integration.error',
      params: { provider: 'AutoScout24', connection: 'Inserate' },
      link: '/administration/integrations',
    }),
    item({
      key: 'nexotec.incident.inc-1',
      source: 'nexotec',
      state: 'warning',
      messageKey: 'statusPage.items.nexotec.incident',
      params: {},
      link: null,
      incident: INCIDENT,
    }),
  ],
}

function install(body: unknown) {
  return installFakeBackend([{ method: 'GET', match: /^\/dealerships\/[^/]+\/status$/, handler: () => body }])
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('de')
})

describe('StatusPage (KAN-223)', () => {
  const t = (key: string, options?: Record<string, unknown>) => i18n.getFixedT('de')(key, options)

  it('says «all green» and lists what it checks', async () => {
    install(GREEN)
    renderWithProviders(<StatusPage />, { route: '/administration/status' })
    expect(await screen.findByText(t('statusPage.overall.ok'))).toBeInTheDocument()
    expect(screen.getByText(t('statusPage.overallDetail.ok'))).toBeInTheDocument()
    const ranges = screen.getByRole('table', { name: t('statusPage.sources.number_ranges') })
    expect(within(ranges).getByText(t('statusPage.items.numberRange.ok', { documentType: 'Offerte', percent: 12 }))).toBeInTheDocument()
    // a green item offers no «Open»
    expect(within(ranges).queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByText(t('statusPage.items.nexotec.ok'))).toBeInTheDocument()
  })

  it('names each item that is not green and links to the screen that fixes it', async () => {
    install(DEGRADED)
    renderWithProviders(<StatusPage />, { route: '/administration/status' })
    expect(await screen.findByText(t('statusPage.overall.blocked'))).toBeInTheDocument()
    expect(screen.getByText(t('statusPage.overallDetail.attention', { count: 2 }))).toBeInTheDocument()

    const integrations = screen.getByRole('table', { name: t('statusPage.sources.integration') })
    const row = within(integrations).getByRole('row', {
      name: (n) => n.includes(t('statusPage.items.integration.error', { provider: 'AutoScout24', connection: 'Inserate' })),
    })
    expect(within(row).getByText(t('statusPage.states.blocked'))).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: t('statusPage.open') })).toHaveAttribute('href', '/administration/integrations')

    const ranges = screen.getByRole('table', { name: t('statusPage.sources.number_ranges') })
    expect(
      within(ranges).getByText(t('statusPage.items.numberRange.nearlyExhausted', { documentType: 'Offerte', percent: 93 })),
    ).toBeInTheDocument()
  })

  it("shows Nexotec's incident in the UI language, with nothing for the dealer to open", async () => {
    install(DEGRADED)
    await i18n.changeLanguage('fr')
    renderWithProviders(<StatusPage />, { route: '/administration/status' })
    const nexotec = await screen.findByRole('table', { name: i18n.getFixedT('fr')('statusPage.sources.nexotec') })
    expect(within(nexotec).getByText('Annonces lentes')).toBeInTheDocument()
    expect(within(nexotec).getByText('Nous y travaillons.')).toBeInTheDocument()
    expect(within(nexotec).queryByRole('link')).not.toBeInTheDocument()
  })

  // KAN-776 — Nexotec's Business Profile client interrupted: one item, never the dealer's to fix.
  it('shows Nexotec\'s interrupted Business Profile connection with nothing to open and no cause', async () => {
    install({
      ...GREEN,
      state: 'blocked',
      attentionCount: 1,
      items: [
        ...GREEN.items,
        item({
          key: 'google_business_profile.client',
          source: 'google_business_profile',
          state: 'blocked',
          messageKey: 'statusPage.items.googleBusinessProfile.interrupted',
          params: {},
          link: null,
        }),
      ],
    })
    renderWithProviders(<StatusPage />, { route: '/administration/status' })
    const gbp = await screen.findByRole('table', { name: t('statusPage.sources.google_business_profile') })
    const row = within(gbp).getByRole('row', { name: (n) => n.includes(t('statusPage.items.googleBusinessProfile.interrupted')) })
    expect(within(row).getByText(t('statusPage.states.blocked'))).toBeInTheDocument()
    expect(within(gbp).queryByRole('link')).not.toBeInTheDocument()
  })
})

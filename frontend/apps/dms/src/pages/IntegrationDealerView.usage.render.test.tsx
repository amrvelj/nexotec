// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderPage, IntegrationUsageRead } from '../api/types'
import { todayLocalIsoDate } from '../utils/format'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-222 (FR-A-23) — «Nutzung anzeigen»: the period's requests per provider
// counter, named verbatim, Swiss-grouped counts, a period picker, and no
// money (Anto, 2026-11-14, Q1 A: counts only).

const providers = (): IntegrationProviderPage => ({
  items: [
    {
      id: 'prov-aid',
      providerCode: 'auto_i_dat',
      displayName: 'auto-i-dat',
      category: 'vehicle_data',
      authType: 'oauth2_client_credentials',
      capabilityCodes: [],
      requiredConfigKeys: ['clientId'],
      requiredSecretSlots: [],
      supportsSandbox: true,
      docsUrl: null,
      version: 1,
    },
  ],
})

const connection: IntegrationConnectionRead = {
  id: 'conn-1',
  providerId: 'prov-aid',
  providerCode: 'auto_i_dat',
  displayName: 'auto-i-dat Garage',
  environment: 'production',
  scope: 'tenant',
  status: 'connected',
  enabled: true,
  config: { clientId: 'svc-garage' },
  tenantId: 'dealership-1',
  accountIdentifier: 'svc-garage',
  locationId: null,
  locationLabel: null,
  locationBindable: false,
  createdAt: '2026-01-01T00:00:00Z',
  expiresAt: null,
  lastError: null,
  lastVerifiedAt: null,
  rotatedAt: null,
  updatedAt: '2026-01-01T00:00:00Z',
  version: 1,
}

function install(usage: (params: URLSearchParams) => IntegrationUsageRead) {
  return installFakeBackend([
    { match: /^\/integrations\/providers$/, handler: () => providers() },
    { match: /^\/integrations\/connections\/conn-1\/usage$/, handler: (req) => usage(req.params) },
    { match: /^\/integrations\/connections$/, handler: () => ({ items: [connection], nextCursor: null, total: 1, totalIsEstimate: false }) },
  ])
}

const usage = (params: URLSearchParams, counters: IntegrationUsageRead['counters']): IntegrationUsageRead => ({
  periodFrom: params.get('from') ?? '',
  periodTo: params.get('to') ?? '',
  totalCalls: counters.reduce((sum, c) => sum + c.count, 0),
  counters,
  indicative: true,
})

async function openUsage() {
  renderWithProviders(<IntegrationDealerView />)
  await userEvent.click(await screen.findByRole('button', { name: 'Nutzung anzeigen' }))
  return screen.findByRole('dialog')
}

describe('IntegrationDealerView usage modal (KAN-222)', () => {
  it('lists each provider counter verbatim with Swiss-grouped counts and a total', async () => {
    install((params) => usage(params, [{ counter: 'caption_code', count: 12500 }, { counter: 'oauth_token', count: 3 }]))
    const dialog = await openUsage()
    expect(await within(dialog).findByText('caption_code')).toBeInTheDocument()
    expect(within(dialog).getByText("12'500")).toBeInTheDocument()
    expect(within(dialog).getByText('oauth_token')).toBeInTheDocument()
    expect(within(dialog).getByText("12'503")).toBeInTheDocument()
    expect(within(dialog).getByText(/Nur indikativ/)).toBeInTheDocument()
    expect(dialog.textContent).not.toMatch(/CHF|Kosten/)
  })

  it('asks for the trailing 30 days by default and for the period the user picks', async () => {
    const backend = install((params) => usage(params, []))
    const dialog = await openUsage()
    expect(await within(dialog).findByText('Keine Anfragen in diesem Zeitraum.')).toBeInTheDocument()
    const first = backend.callsTo(/\/usage$/)[0]
    expect(first.params.get('to')).toBe(todayLocalIsoDate())
    const from = new Date(`${first.params.get('from')}T00:00:00Z`).getTime()
    const to = new Date(`${first.params.get('to')}T00:00:00Z`).getTime()
    expect((to - from) / 86_400_000).toBe(29)

    const fromInput = within(dialog).getByLabelText('Von')
    await userEvent.clear(fromInput)
    await userEvent.type(fromInput, '2026-03-01')
    await waitFor(() => expect(backend.callsTo(/\/usage$/).some((c) => c.params.get('from') === '2026-03-01')).toBe(true))
  })

  it('refuses a period that ends before it starts without asking the API', async () => {
    const backend = install((params) => usage(params, []))
    const dialog = await openUsage()
    await within(dialog).findByText('Keine Anfragen in diesem Zeitraum.')
    const before = backend.callsTo(/\/usage$/).length
    const fromInput = within(dialog).getByLabelText('Von')
    await userEvent.clear(fromInput)
    await userEvent.type(fromInput, '2999-01-01')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Das Ende des Zeitraums liegt vor seinem Beginn.')
    expect(backend.callsTo(/\/usage$/).length).toBe(before)
  })
})

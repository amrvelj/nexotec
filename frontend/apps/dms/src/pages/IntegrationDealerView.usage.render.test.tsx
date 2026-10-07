// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderPage, IntegrationUsageRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-164 — the usage modal rendered costUnitsThisPeriod as the raw decimal
// string (`1234.5678`): no Swiss grouping. The column is DECIMAL(12, 4), so
// it must also keep all four decimals, which formatNumber's default (Intl's
// three) would round away.

const providers = (): IntegrationProviderPage => ({
  items: [
    {
      id: 'prov-aid',
      providerCode: 'auto_i_dat',
      displayName: 'auto-i-dat',
      category: 'vehicle_data',
      authType: 'api_key',
      capabilityCodes: [],
      requiredConfigKeys: [],
      requiredSecretSlots: [],
      supportsSandbox: false,
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
  config: { username: 'garage' },
  tenantId: 'dealership-1',
  createdAt: '2026-01-01T00:00:00Z',
  expiresAt: null,
  lastError: null,
  lastVerifiedAt: null,
  rotatedAt: null,
  updatedAt: '2026-01-01T00:00:00Z',
  version: 1,
}

function install(usage: IntegrationUsageRead) {
  return installFakeBackend([
    { match: /^\/integrations\/providers$/, handler: () => providers() },
    { match: /^\/integrations\/connections\/conn-1\/usage$/, handler: () => usage },
    { match: /^\/integrations\/connections$/, handler: () => ({ items: [connection], nextCursor: null, total: 1, totalIsEstimate: false }) },
  ])
}

async function openUsage() {
  renderWithProviders(<IntegrationDealerView />)
  await userEvent.click(await screen.findByRole('button', { name: 'Nutzung anzeigen' }))
}

describe('IntegrationDealerView usage modal — cost units (KAN-164)', () => {
  it('groups cost units Swiss-style and keeps all four decimals', async () => {
    install({ callsThisPeriod: 12500, costUnitsThisPeriod: '1234.5678', indicative: true })
    await openUsage()
    expect(await screen.findByText("1'234.5678")).toBeInTheDocument()
    expect(screen.getByText("12'500")).toBeInTheDocument()
    expect(screen.queryByText('1234.5678')).not.toBeInTheDocument()
  })

  it('pads cost units to the four decimals of the column', async () => {
    install({ callsThisPeriod: 0, costUnitsThisPeriod: '12.5', indicative: true })
    await openUsage()
    expect(await screen.findByText('12.5000')).toBeInTheDocument()
  })

  it('renders a dash when no cost was attributed', async () => {
    install({ callsThisPeriod: 3, costUnitsThisPeriod: null, indicative: true })
    await openUsage()
    // The connection card behind the modal shows dashes of its own.
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('—')).toBeInTheDocument()
  })
})

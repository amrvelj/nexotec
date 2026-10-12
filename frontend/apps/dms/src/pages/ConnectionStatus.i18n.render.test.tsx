// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'
import { IntegrationPlatformView } from './IntegrationPlatformView'

// KAN-181 — the ui-kit's ConnectionStatusBadge carried German labels
// ("Verbunden", "Fehler", …) and both integration views rendered it without a
// label, so the connection status was German under FR, IT and EN. Both views
// now pass the translated label; the kit badge has no default.

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => {
    const size = estimateSize()
    return {
      getVirtualItems: () => Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * size, size })),
      getTotalSize: () => count * size,
      measure: () => {},
    }
  },
}))

const PROVIDER = {
  id: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', category: 'vehicle_data', authType: 'soap',
  capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: ['password'], supportsSandbox: true,
  docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

const connection = (status: IntegrationConnectionRead['status']) =>
  ({
    id: 'conn-1', tenantId: 'dealership-1', providerId: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat',
    environment: 'sandbox', status, enabled: true, config: {}, lastVerifiedAt: null, lastError: null, version: 1,
  }) as unknown as IntegrationConnectionRead

const page = (items: IntegrationConnectionRead[]) => ({ items, nextCursor: null, total: items.length, totalIsEstimate: false })

// Hardcoded on purpose — never derived through i18n.t() the way the view
// derives it, or a key missing on both sides would pass trivially.
const FRENCH = { connected: 'Connectée', error: 'Erreur' }
const ITALIAN = { expired: 'Scaduta' }
const GERMAN = ['Verbunden', 'Fehler', 'Abgelaufen', 'Nicht konfiguriert', 'Deaktiviert']

afterEach(async () => {
  await i18n.changeLanguage('de')
})

describe('IntegrationDealerView — the connection status is translated (KAN-181)', () => {
  it('renders a connected connection as "Connecté" under the French UI, never the German default', async () => {
    await i18n.changeLanguage('fr')
    installFakeBackend([
      { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
      { method: 'GET', match: /^\/integrations\/connections$/, handler: () => page([connection('connected')]) },
    ])
    renderWithProviders(<IntegrationDealerView />)

    expect(await screen.findByText(FRENCH.connected)).toBeInTheDocument()
    for (const word of GERMAN) expect(screen.queryByText(word)).not.toBeInTheDocument()
  })
})

describe('IntegrationPlatformView — the status column is translated (KAN-181)', () => {
  function install(items: IntegrationConnectionRead[]) {
    installFakeBackend([
      { match: /^\/dealerships/, handler: () => ({ items: [], nextCursor: null, total: 0, totalIsEstimate: false }) },
      { match: /^\/integrations\/connections/, handler: () => page(items) },
      { match: /^\/vehicle-mdm\/catalogue-sync-status$/, handler: () => [] },
    ])
  }

  it('renders an errored connection as "Erreur" under the French UI', async () => {
    await i18n.changeLanguage('fr')
    install([connection('error')])
    renderWithProviders(<IntegrationPlatformView />)

    await waitFor(() => expect(screen.getByText(FRENCH.error)).toBeInTheDocument())
    for (const word of GERMAN) expect(screen.queryByText(word)).not.toBeInTheDocument()
  })

  it('renders an expired connection as "Scaduta" under the Italian UI', async () => {
    await i18n.changeLanguage('it')
    install([connection('expired')])
    renderWithProviders(<IntegrationPlatformView />)

    await waitFor(() => expect(screen.getByText(ITALIAN.expired)).toBeInTheDocument())
    for (const word of GERMAN) expect(screen.queryByText(word)).not.toBeInTheDocument()
  })
})

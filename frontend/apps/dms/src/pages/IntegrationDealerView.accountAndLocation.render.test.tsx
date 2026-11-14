// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import { pickOptionWithoutOpening } from '../test/pickOption'
import type { IntegrationConnectionRead, IntegrationProviderPage } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-222 — every card names the account at the provider (FR-A-20, «Kennung
// fehlt» when the configuration lacks it) and, for a per-site account, the
// location it serves (FR-A-21), which the manager assigns from the card.

const provider = (id: string, code: string, name: string, category: string) => ({
  id,
  providerCode: code,
  displayName: name,
  category,
  authType: 'password',
  capabilityCodes: [],
  requiredConfigKeys: [],
  requiredSecretSlots: [],
  supportsSandbox: false,
  docsUrl: null,
  version: 1,
})

const providers = (): IntegrationProviderPage => ({
  items: [
    provider('prov-as24', 'autoscout24', 'AutoScout24', 'marketplace'),
    provider('prov-aid', 'auto_i_dat', 'auto-i-dat', 'vehicle_data'),
  ],
})

const base = {
  environment: 'production' as const,
  scope: 'tenant' as const,
  status: 'connected' as const,
  enabled: true,
  tenantId: 'dealership-1',
  createdAt: '2026-01-01T00:00:00Z',
  expiresAt: null,
  lastError: null,
  lastVerifiedAt: null,
  rotatedAt: null,
  updatedAt: '2026-01-01T00:00:00Z',
}

const as24 = (overrides: Partial<IntegrationConnectionRead> = {}): IntegrationConnectionRead => ({
  ...base,
  id: 'conn-as24',
  providerId: 'prov-as24',
  providerCode: 'autoscout24',
  displayName: 'AutoScout24 Nord',
  config: { kundennummer: 'K-4711' },
  accountIdentifier: 'K-4711',
  locationId: null,
  locationLabel: null,
  locationBindable: true,
  version: 3,
  ...overrides,
})

const aid: IntegrationConnectionRead = {
  ...base,
  id: 'conn-aid',
  providerId: 'prov-aid',
  providerCode: 'auto_i_dat',
  displayName: 'auto-i-dat',
  config: {},
  accountIdentifier: null,
  locationId: null,
  locationLabel: null,
  locationBindable: false,
  version: 1,
}

function install(connections: IntegrationConnectionRead[]) {
  return installFakeBackend([
    { match: /^\/integrations\/providers$/, handler: () => providers() },
    {
      method: 'PATCH',
      match: /^\/integrations\/connections\/conn-as24$/,
      handler: () => as24({ locationId: 'loc-2', locationLabel: 'Winterthur', version: 4 }),
    },
    {
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: connections, nextCursor: null, total: connections.length, totalIsEstimate: false }),
    },
    {
      match: /^\/dealerships\/dealership-1\/locations$/,
      handler: () => ({
        items: [
          { id: 'loc-1', name: 'Zürich Nord', isActive: true },
          { id: 'loc-2', name: 'Winterthur', isActive: true },
          { id: 'loc-3', name: 'Alt', isActive: false },
        ],
      }),
    },
  ])
}

describe('IntegrationDealerView — account and location on the card (KAN-222)', () => {
  it('names the account from the API and says «Kennung fehlt» when it is missing', async () => {
    install([as24(), aid])
    renderWithProviders(<IntegrationDealerView />)
    expect(await screen.findByText('K-4711')).toBeInTheDocument()
    expect(screen.getByText('Kennung fehlt')).toBeInTheDocument()
  })

  it('shows the location only for a per-site account, «Ganzer Betrieb» while unbound', async () => {
    install([as24(), aid])
    renderWithProviders(<IntegrationDealerView />)
    await screen.findByText('K-4711')
    expect(screen.getAllByText('Standort')).toHaveLength(1)
    expect(screen.getByText('Ganzer Betrieb')).toBeInTheDocument()
  })

  it('shows the bound location by its name', async () => {
    install([as24({ locationId: 'loc-1', locationLabel: 'Zürich Nord' })])
    renderWithProviders(<IntegrationDealerView />)
    expect(await screen.findByText('Zürich Nord')).toBeInTheDocument()
  })

  it('assigns a location from the row menu with If-Match, offering active locations only', async () => {
    const backend = install([as24()])
    renderWithProviders(<IntegrationDealerView />)
    await screen.findByText('K-4711')
    await userEvent.click(screen.getByRole('button', { name: 'Zeilenaktionen' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Standort zuordnen' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('option', { name: 'Zürich Nord', hidden: true })
    // A deactivated location is not offered for a new binding.
    expect(within(dialog).queryByRole('option', { name: 'Alt', hidden: true })).not.toBeInTheDocument()
    await pickOptionWithoutOpening('Winterthur')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(backend.callsTo(/\/integrations\/connections\/conn-as24$/, 'PATCH')).toHaveLength(1))
    const patch = backend.callsTo(/\/integrations\/connections\/conn-as24$/, 'PATCH')[0]
    expect(patch.body).toEqual({ locationId: 'loc-2' })
    expect(patch.headers.get('If-Match')).toBe('3')
  })

  it('offers no «Standort zuordnen» on a connection that serves the whole Betrieb', async () => {
    install([aid])
    renderWithProviders(<IntegrationDealerView />)
    await screen.findByText('Kennung fehlt')
    await userEvent.click(screen.getByRole('button', { name: 'Zeilenaktionen' }))
    await screen.findByRole('menuitem', { name: 'Verbindung testen' })
    expect(screen.queryByRole('menuitem', { name: 'Standort zuordnen' })).not.toBeInTheDocument()
  })
})

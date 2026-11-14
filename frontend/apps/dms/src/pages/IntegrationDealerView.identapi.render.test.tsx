// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-488 (ADR-111) — the auto-i-dat account is an OAuth 2.0 client: the
// Connect dialog renders the provider row's fields (Client-ID, a config key;
// Client-Secret, a write-only secret slot) and the card shows the client id as
// the account identifier — never the secret, which `config` never carries.

const provider: IntegrationProviderRead = {
  id: 'prov-aid',
  providerCode: 'auto_i_dat',
  displayName: 'auto-i-dat (identAPI)',
  category: 'vehicle_data',
  authType: 'oauth2_user_enrollment',
  capabilityCodes: ['ident_basis', 'ausstattung', 'bewertung', 'vin', 'pneu', 'energielabel'],
  requiredConfigKeys: ['clientId'],
  requiredSecretSlots: ['client_secret'],
  supportsSandbox: true,
  docsUrl: null,
  version: 1,
} as unknown as IntegrationProviderRead

const connection: IntegrationConnectionRead = {
  id: 'conn-1',
  providerId: 'prov-aid',
  providerCode: 'auto_i_dat',
  displayName: 'auto-i-dat Garage',
  environment: 'sandbox',
  scope: 'tenant',
  status: 'connected',
  enabled: true,
  config: { clientId: 'client-id-for-the-test' },
  accountIdentifier: 'client-id-for-the-test',
  tenantId: 'dealership-1',
  createdAt: '2026-01-01T00:00:00Z',
  expiresAt: null,
  lastError: null,
  lastVerifiedAt: null,
  rotatedAt: null,
  updatedAt: '2026-01-01T00:00:00Z',
  version: 1,
} as unknown as IntegrationConnectionRead

function install(connections: IntegrationConnectionRead[]) {
  installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [provider] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections/,
      handler: () => ({ items: connections, nextCursor: null, total: connections.length, totalIsEstimate: false }),
    },
  ])
}

afterEach(() => cleanup())

describe('IntegrationDealerView — the identAPI account shape (KAN-488)', () => {
  it('the Connect dialog asks for the Client-ID and the Client-Secret, nothing else of the account', async () => {
    install([])
    const user = userEvent.setup()
    renderWithProviders(<IntegrationDealerView />)
    await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByLabelText('Client-ID')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Client-Secret')).toHaveAttribute('type', 'password')
    expect(within(dialog).queryByLabelText(/BenutzerNr/)).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/AES/)).not.toBeInTheDocument()
  })

  it('a connected card names the account by its client id', async () => {
    install([connection])
    renderWithProviders(<IntegrationDealerView />)

    expect(await screen.findByText('client-id-for-the-test')).toBeInTheDocument()
  })
})

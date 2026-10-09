// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-266 — the dealer's integration screen sends one Idempotency-Key per
// submission: "Verbindung testen" (a logged, possibly billed provider call)
// and the connect dialog's create, which keeps its key until the secrets are
// written too.

const PROVIDER = {
  id: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', category: 'vehicle_data', authType: 'soap',
  capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: ['password'], supportsSandbox: true,
  docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

const CONNECTION = {
  id: 'conn-1', providerId: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', environment: 'sandbox',
  status: 'connected', enabled: true, config: {}, lastVerifiedAt: null, lastError: null, version: 1,
} as unknown as IntegrationConnectionRead

const unavailable = () => status(503, { error: { code: 'unavailable', message: 'Service unavailable', details: null } })

function install({ connected = true, failFirstCreate = false, failFirstSecret = false } = {}) {
  let creates = 0
  let secrets = 0
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: connected ? [CONNECTION] : [], nextCursor: null, total: 0, totalIsEstimate: false }),
    },
    { method: 'POST', match: /^\/integrations\/connections\/conn-1\/test$/, handler: () => CONNECTION },
    {
      method: 'POST',
      match: /^\/integrations\/connections$/,
      handler: () => {
        creates += 1
        if (failFirstCreate && creates === 1) return unavailable()
        return { __status: 201, body: CONNECTION }
      },
    },
    {
      method: 'PUT',
      match: /^\/integrations\/connections\/conn-1\/secrets\/password$/,
      handler: () => {
        secrets += 1
        if (failFirstSecret && secrets === 1) return unavailable()
        return { slot: 'password', rotatedAt: null }
      },
    },
  ])
}

const keysTo = (backend: ReturnType<typeof install>, pattern: RegExp) =>
  backend.callsTo(pattern, 'POST').map((call) => call.headers.get('Idempotency-Key'))

async function connect(user: ReturnType<typeof userEvent.setup>) {
  const dialog = await screen.findByRole('dialog')
  const password = within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.password', 'password'))
  await user.clear(password)
  await user.type(password, 's3cret')
  await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
}

afterEach(() => cleanup())

describe('IntegrationDealerView — one Idempotency-Key per submission (KAN-266)', () => {
  it('two connection tests in a row are two submissions, each under its own key', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<IntegrationDealerView />)

    const test = await screen.findByRole('button', { name: i18n.t('integrationsList.actions.test') })
    await user.click(test)
    await waitFor(() => expect(keysTo(backend, /\/test$/)).toHaveLength(1))
    await user.click(test)
    await waitFor(() => expect(keysTo(backend, /\/test$/)).toHaveLength(2))

    const [first, second] = keysTo(backend, /\/test$/)
    expect(first).toBeTruthy()
    expect(second).toBeTruthy()
    expect(second).not.toBe(first)
  })

  it('after a failed secret write, connecting again replays the created connection under the same key', async () => {
    const user = userEvent.setup()
    const backend = install({ connected: false, failFirstSecret: true })
    renderWithProviders(<IntegrationDealerView />)

    await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    await connect(user)
    expect(await screen.findByText('Service unavailable')).toBeInTheDocument()
    await connect(user)
    await waitFor(() => expect(backend.callsTo(/\/secrets\/password$/, 'PUT')).toHaveLength(2))

    const [first, retry] = keysTo(backend, /^\/integrations\/connections$/)
    expect(first).toBeTruthy()
    expect(retry).toBe(first)
  })

  it('reopening the connect dialog after a failure gets a new key', async () => {
    const user = userEvent.setup()
    const backend = install({ connected: false, failFirstCreate: true })
    renderWithProviders(<IntegrationDealerView />)

    const open = await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') })
    await user.click(open)
    await connect(user)
    await screen.findByText('Service unavailable')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: i18n.t('common.cancel') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    await connect(user)
    await waitFor(() => expect(keysTo(backend, /^\/integrations\/connections$/)).toHaveLength(2))

    const [first, reopened] = keysTo(backend, /^\/integrations\/connections$/)
    expect(first).toBeTruthy()
    expect(reopened).toBeTruthy()
    expect(reopened).not.toBe(first)
  })
})

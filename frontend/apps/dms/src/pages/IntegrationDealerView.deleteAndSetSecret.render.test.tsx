// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-603 (staging, 2026-11-09) — a connection whose secret write failed had
// no secret, «Secret erneuern» was greyed out and nothing on the screen could
// delete it: four broken connections were deleted from the browser console.
// Now: «Verbindung löschen» on the card, «Secret setzen» for a slot never
// stored, and the connect dialog says when the create worked but the secret
// did not.

const PROVIDER = {
  id: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', category: 'vehicle_data',
  authType: 'oauth2_user_enrollment', capabilityCodes: [], requiredConfigKeys: ['clientId'],
  requiredSecretSlots: ['client_secret'], supportsSandbox: true, docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

function connection(slots: string[] = []): IntegrationConnectionRead {
  return {
    id: 'conn-1', providerId: 'prov-1', providerCode: 'auto_i_dat', displayName: 'auto-i-dat', environment: 'sandbox',
    status: 'not_configured', enabled: true, config: { clientId: 'client-4711' }, accountIdentifier: 'client-4711', lastVerifiedAt: null, lastError: null,
    version: 3, secretSlots: slots.map((slot) => ({ slot, rotatedAt: null })),
  } as unknown as IntegrationConnectionRead
}

const NOT_CONFIGURED = {
  error: {
    code: 'conflict',
    message: 'No Infisical project is configured for this deployment.',
    details: { reason: 'secret_store_not_configured' },
  },
}

function install({ connections = [connection()], secretWrite = () => ({ slot: 'client_secret', rotatedAt: null }) as unknown } = {}) {
  let list = connections
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: list, nextCursor: null, total: list.length, totalIsEstimate: false }),
    },
    {
      method: 'POST',
      match: /^\/integrations\/connections$/,
      handler: () => {
        list = [connection()]
        return { __status: 201, body: connection() }
      },
    },
    {
      method: 'DELETE',
      match: /^\/integrations\/connections\/conn-1$/,
      handler: () => {
        list = []
        return { __status: 204 }
      },
    },
    { method: 'PUT', match: /^\/integrations\/connections\/conn-1\/secrets\/client_secret$/, handler: () => secretWrite() },
  ])
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: i18n.t('common.rowActionsLabel') }))
}

afterEach(() => cleanup())

describe('IntegrationDealerView — delete a connection from its card (KAN-603)', () => {
  it('asks first, naming provider, environment and account, then sends If-Match and confirm', async () => {
    const user = userEvent.setup()
    const backend = install({ connections: [connection(['client_secret'])] })
    renderWithProviders(<IntegrationDealerView />)

    await openMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.delete') }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(
      i18n.t('integrationsList.deleteModal.body', {
        provider: 'auto-i-dat',
        environment: i18n.t('integrationEnums.environment.sandbox'),
        account: 'client-4711',
      })
    )
    expect(dialog).toHaveTextContent(i18n.t('integrationsList.deleteModal.warning'))
    expect(backend.callsTo(/\/connections\/conn-1$/, 'DELETE')).toHaveLength(0)

    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.deleteModal.confirm') }))

    await waitFor(() => expect(backend.callsTo(/\/connections\/conn-1$/, 'DELETE')).toHaveLength(1))
    const [call] = backend.callsTo(/\/connections\/conn-1$/, 'DELETE')
    expect(call.headers.get('If-Match')).toBe('3')
    expect(call.body).toEqual({ confirm: true })
    // The list is fetched again: the card is back to «Verbinden».
    expect(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') })).toBeInTheDocument()
  })

  it('cancelling sends nothing', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<IntegrationDealerView />)

    await openMenu(user)
    await user.click(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.delete') }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: i18n.t('common.cancel') }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(backend.callsTo(/\/connections\/conn-1$/, 'DELETE')).toHaveLength(0)
  })
})

describe('IntegrationDealerView — set a secret that was never stored (KAN-603)', () => {
  it('offers «Secret setzen» on a connection without secrets, lists the unset slot and writes it', async () => {
    const user = userEvent.setup()
    const backend = install()
    renderWithProviders(<IntegrationDealerView />)

    await openMenu(user)
    const item = await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.setSecret') })
    expect(item).toBeEnabled()
    expect(screen.queryByRole('menuitem', { name: i18n.t('integrationsList.actions.rotateSecret') })).not.toBeInTheDocument()
    await user.click(item)

    const dialog = await screen.findByRole('dialog')
    const field = within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret'))
    expect(field).toHaveAttribute('placeholder', i18n.t('integrationsList.rotateModal.notSet'))
    await user.type(field, 'n3w-s3cret')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.setSecret') }))

    await waitFor(() => expect(backend.callsTo(/\/secrets\/client_secret$/, 'PUT')).toHaveLength(1))
    expect(backend.callsTo(/\/secrets\/client_secret$/, 'PUT')[0].body).toEqual({ secretValue: 'n3w-s3cret' })
  })

  it('keeps «Secret erneuern» for a connection whose secret is stored', async () => {
    const user = userEvent.setup()
    install({ connections: [connection(['client_secret'])] })
    renderWithProviders(<IntegrationDealerView />)

    await openMenu(user)
    expect(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.rotateSecret') })).toBeEnabled()
  })
})

describe('IntegrationDealerView — the connect dialog says what failed (KAN-603, staging 2026-11-09)', () => {
  it.each([
    ['409, no secret store', () => status(409, NOT_CONFIGURED), () => i18n.t('apiRefusal.reasons.secret_store_not_configured')],
    [
      '500 from the secrets manager',
      () => status(500, { error: { code: 'internal', message: 'boom', details: null } }),
      () => i18n.t('integrationsList.connectModal.secretFailedFallback'),
    ],
  ])('create 201, secret write %s: names the secret failure, never «konnte nicht erstellt werden»', async (_, refusal, reason) => {
    const user = userEvent.setup()
    install({ connections: [], secretWrite: refusal })
    renderWithProviders(<IntegrationDealerView />)

    await user.click(await screen.findByRole('button', { name: i18n.t('integrationsList.actions.connect') }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.configKey.clientId', 'clientId')), 'client-4711')
    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret')), 's3cret')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.connect') }))

    const alert = await within(screen.getByRole('dialog')).findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('integrationsList.connectModal.secretFailed', { reason: reason() }))
    expect(screen.queryByText(i18n.t('integrationsList.connectError'))).not.toBeInTheDocument()
    expect(screen.queryByText(/besteht bereits/)).not.toBeInTheDocument()

    // It closes onto the card, where «Secret setzen» repairs it.
    await user.click(screen.getByRole('button', { name: i18n.t('integrationsList.connectModal.toConnection') }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await openMenu(user)
    expect(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.setSecret') })).toBeEnabled()
  })
})

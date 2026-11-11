// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import i18n from '../i18n'
import { renderWithProviders } from '../test/renderWithProviders'
import { installFakeBackend, status } from '../test/fakeBackend'
import type { IntegrationConnectionRead, IntegrationProviderRead } from '../api/types'
import { IntegrationDealerView } from './IntegrationDealerView'

// KAN-607 — the rotate dialog had a try/finally and no catch: a refused
// secret write left the dialog open with no message, and with several slots
// nobody was told that the slots before the refused one were already
// replaced. A refusal is now said through `apiRefusalText` (KAN-437), the
// typed values stay, and the slots already saved are named.

const PROVIDER = {
  id: 'prov-1', providerCode: 'autoscout24', displayName: 'AutoScout24', category: 'marketplace', authType: 'password',
  capabilityCodes: [], requiredConfigKeys: [], requiredSecretSlots: ['password'], supportsSandbox: true,
  docsUrl: null, version: 1,
} as unknown as IntegrationProviderRead

function connection(slots: string[]): IntegrationConnectionRead {
  return {
    id: 'conn-1', providerId: 'prov-1', providerCode: 'autoscout24', displayName: 'AutoScout24', environment: 'sandbox',
    status: 'connected', enabled: true, config: {}, lastVerifiedAt: null, lastError: null, version: 1,
    secretSlots: slots.map((slot) => ({ slot, rotatedAt: null })),
  } as unknown as IntegrationConnectionRead
}

const REFUSAL = {
  error: {
    code: 'conflict',
    message: "The secrets manager refused 'update_secret' (HTTP 500).",
    details: { reason: 'secrets_backend_failed', operation: 'update_secret', status: 500 },
  },
}

function install(slots: string[], refusedSlot: string) {
  return installFakeBackend([
    { method: 'GET', match: /^\/integrations\/providers$/, handler: () => ({ items: [PROVIDER] }) },
    {
      method: 'GET',
      match: /^\/integrations\/connections$/,
      handler: () => ({ items: [connection(slots)], nextCursor: null, total: 1, totalIsEstimate: false }),
    },
    {
      method: 'PUT',
      match: /^\/integrations\/connections\/conn-1\/secrets\/[a-z_]+$/,
      handler: (req) =>
        req.pathname.endsWith(`/${refusedSlot}`) ? status(409, REFUSAL) : { slot: req.pathname.split('/').pop(), rotatedAt: null },
    },
  ])
}

async function openRotateDialog() {
  const user = userEvent.setup()
  renderWithProviders(<IntegrationDealerView />)
  await user.click(await screen.findByRole('button', { name: i18n.t('common.rowActionsLabel') }))
  await user.click(await screen.findByRole('menuitem', { name: i18n.t('integrationsList.actions.rotateSecret') }))
  return { user, dialog: await screen.findByRole('dialog') }
}

afterEach(() => cleanup())

describe('IntegrationDealerView — a refused rotation is said, not swallowed (KAN-607)', () => {
  it('shows the translated refusal and keeps the dialog open with the typed value', async () => {
    const backend = install(['password'], 'password')
    const { user, dialog } = await openRotateDialog()
    const field = within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.password'))

    await user.type(field, 'n3w-passw0rd')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.rotateSecret') }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('apiRefusal.reasons.secrets_backend_failed'))
    // The API's English message is for logs only.
    expect(alert).not.toHaveTextContent('The secrets manager refused')
    expect(screen.getByRole('dialog')).toBe(dialog)
    expect(field).toHaveValue('n3w-passw0rd')
    // One slot, refused: nothing was saved, so no «already rotated» line.
    expect(alert.textContent).toBe(i18n.t('apiRefusal.reasons.secrets_backend_failed'))
    expect(backend.callsTo(/\/secrets\//, 'PUT')).toHaveLength(1)
  })

  it('names the slots already rotated before a later one was refused', async () => {
    install(['password', 'client_secret'], 'client_secret')
    const { user, dialog } = await openRotateDialog()

    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.password')), 'pw-1')
    await user.type(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret')), 'cs-1')
    await user.click(within(dialog).getByRole('button', { name: i18n.t('integrationsList.actions.rotateSecret') }))

    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('apiRefusal.reasons.secrets_backend_failed'))
    expect(alert).toHaveTextContent(
      i18n.t('integrationsList.rotateModal.savedBeforeRefusal', { slots: i18n.t('integrationEnums.secretSlot.password') }),
    )
    expect(within(dialog).getByLabelText(i18n.t('integrationEnums.secretSlot.client_secret'))).toHaveValue('cs-1')
  })
})
